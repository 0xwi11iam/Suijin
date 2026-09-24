"""Journal resume — engagement continuation from events.jsonl, not a bundle.

The log is the durable truth: replay() reconstructs AgentState (messages,
trace, iteration/phase, and the per-iteration state.snapshot for the
multi-value fields), and resume hands it to the runner. Finished
engagements are refused — a replayed run MUST run.
"""

import pytest

from suijin.modules.agent.lib.event_log import EventLog
from suijin.modules.ops.lib import journal_resume as jr


@pytest.fixture()
def journal(tmp_path):
    el = EventLog(tmp_path / "events.jsonl")
    el.append("session.start", objective="recon http://t")
    el.append("iteration", n=1, phase="informational")
    el.append("assistant.message", content='{"action": "use_tool", "tool_name": "http_request"}')
    el.append("tool.call", id="t1", name="http_request", args={"url": "http://t/"})
    el.append("tool.result", id="t1", ok=True, output="200 OK")
    el.append("iteration", n=2, phase="exploitation")
    el.append(
        "state.snapshot",
        current_iteration=2,
        current_phase="exploitation",
        todo_list=[{"task": "escalate"}],
        findings=[{"class": "idor", "target": "http://t/1"}],
    )
    el.close()
    return tmp_path


def test_resolve_journal_by_dir_and_file(journal, tmp_path):
    jd = journal / "events.jsonl"
    assert jr._resolve_journal(str(journal)) == jd
    assert jr._resolve_journal(str(jd)) == jd
    assert jr._resolve_journal("some_slug").name == "events.jsonl"


def test_resume_refuses_finished(journal):
    EventLog(journal / "events.jsonl").append("session.complete", reason="Objective complete")
    with pytest.raises(ValueError, match="COMPLETED"):
        jr.resume_from_journal(str(journal))


def test_resume_refuses_non_journal(tmp_path):
    with pytest.raises(ValueError, match="no engagement journal"):
        jr.resume_from_journal(str(tmp_path))


def test_resume_refuses_empty_snapshot(tmp_path):
    el = EventLog(tmp_path / "events.jsonl")
    el.append("session.start", objective="x")
    el.close()
    with pytest.raises(ValueError, match="no work to resume"):
        jr.resume_from_journal(str(tmp_path))


def test_resume_launches_with_reconstructed_state(journal, monkeypatch):
    calls = {}

    def fake_run(config, objective, *, resume_state=None, **kw):
        calls["objective"] = objective
        calls.update(resume_state or {})
        return 7

    import suijin.modules.redteam.lib.redteamer as _runner

    monkeypatch.setattr(_runner, "run_red_team", fake_run)
    code = jr.resume_from_journal(str(journal))
    assert code == 7
    # reconstructed multi-value state rides through (from the snapshot)
    assert calls["todo_list"] == [{"task": "escalate"}]
    assert calls["findings"] == [{"class": "idor", "target": "http://t/1"}]
    assert calls["current_iteration"] == 2
    assert calls["current_phase"] == "exploitation"
    # objective carried from the journal
    assert calls["objective"] == "recon http://t"
    # completion_reason scrubbed: a replayed run must run
    assert calls.get("completion_reason") is None


def test_is_resumable_filter(journal, tmp_path):
    jd = journal / "events.jsonl"
    assert jr._is_resumable(jd)
    # trailing session.complete → finished, not resumable
    EventLog(jd).append("session.complete", reason="done")
    assert not jr._is_resumable(jd)
    # trailing interrupt → resumable (a crash/pause is the whole point)
    el = EventLog(tmp_path / "i.jsonl")
    el.append("session.start", objective="i")
    el.append("session.interrupt", reason="crash")
    el.close()
    assert jr._is_resumable(tmp_path / "i.jsonl")
    # no session.start → not a journal
    (tmp_path / "garbage.jsonl").write_text("{not json}\n")
    assert not jr._is_resumable(tmp_path / "garbage.jsonl")


def test_resumable_engagements_never_raises():
    rows = jr.resumable_engagements()
    assert isinstance(rows, list)
    for r in rows:
        assert {"slug", "dir", "events", "iteration", "phase", "last_at"} <= set(r)


def test_cli_verb_registered():
    from suijin.modules.console.lib.cli import _KNOWN_VERBS, is_known_verb

    assert "resume" in _KNOWN_VERBS and is_known_verb("resume")
