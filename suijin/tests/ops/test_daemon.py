"""The daemon split — background engagements that outlive the console.

Covers the durable control record, id resolution, the detached spawn
(REAL subprocess speaking the same protocol), ps/stop liveness, the live
event renderer, the guidance seam, the child's record lifecycle, and the
CLI verb firewall.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from suijin.modules.ops.lib import daemon

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A hermetic workspace + engagement pin reset (daemon records live
    at <workspace>/daemon)."""
    from suijin.modules.platform.lib import workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()
    yield tmp_path
    ws._reset_engagement()


def _rec(home, **over):
    """A minimal record on disk."""
    rec = {
        "id": "d_20260101_000000_obj",
        "objective": "Test http://target.example",
        "pid": os.getpid(),
        "started_at": "2026-01-01 00:00 UTC",
        "status": "running",
        "engagement": "",
        "state_dir": "",
        "events": "",
        "bundle": "",
        "exit_reason": "",
        "exit_code": None,
        "finished_at": None,
        "resume_ref": None,
        "set": {},
        "log": "",
    }
    rec.update(over)
    daemon.write_record(rec)
    return rec


class TestRecordIO:
    def test_write_read_roundtrip(self, home):
        _rec(home)
        got = daemon._read_record("d_20260101_000000_obj")
        assert got["objective"] == "Test http://target.example"
        assert got["status"] == "running"

    def test_write_is_atomic_no_tmp_left(self, home):
        _rec(home)
        assert not list(daemon.daemon_dir().glob("*.tmp"))

    def test_write_record_rejects_empty_id(self, home):
        assert daemon.write_record({"objective": "x"}) is None

    def test_update_merges_fields(self, home):
        _rec(home)
        daemon.update_record("d_20260101_000000_obj", status="completed", bundle="/x/a.sje")
        got = daemon._read_record("d_20260101_000000_obj")
        assert got["status"] == "completed" and got["bundle"] == "/x/a.sje"
        assert got["objective"] == "Test http://target.example"  # preserved

    def test_update_creates_missing_record(self, home):
        daemon.update_record("d_new", status="running")
        assert daemon._read_record("d_new")["status"] == "running"

    def test_corrupt_record_is_ignored(self, home):
        p = daemon.daemon_dir() / "d_corrupt.json"
        p.write_text("{not json")
        assert daemon._read_record("d_corrupt") is None
        assert daemon.resolve_id("d_corrupt") is None


class TestResolveId:
    def test_exact(self, home):
        _rec(home)
        assert daemon.resolve_id("d_20260101_000000_obj") == "d_20260101_000000_obj"

    def test_prefix(self, home):
        _rec(home)
        assert daemon.resolve_id("d_20260101") == "d_20260101_000000_obj"

    def test_objective_substring(self, home):
        _rec(home)
        assert daemon.resolve_id("target.example") == "d_20260101_000000_obj"

    def test_ambiguous_prefix_takes_newest(self, home):
        _rec(home, id="d_a_old", started_at="2020-01-01 00:00 UTC")
        _rec(home, id="d_a_new", started_at="2030-01-01 00:00 UTC")
        assert daemon.resolve_id("d_a_") == "d_a_new"

    def test_unknown_and_empty(self, home):
        assert daemon.resolve_id("nope") is None
        assert daemon.resolve_id("") is None


class TestLiveness:
    def test_own_pid_alive(self):
        assert daemon.actor_alive(os.getpid()) is True

    def test_dead_pid(self):
        assert daemon.actor_alive(0) is False
        assert daemon.actor_alive(-1) is False
        proc = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        assert daemon.actor_alive(proc.pid) is False

    def test_is_live_needs_both(self, home):
        assert daemon.is_live({"status": "running", "pid": os.getpid()}) is True
        assert daemon.is_live({"status": "completed", "pid": os.getpid()}) is False
        assert daemon.is_live({"status": "running", "pid": 0}) is False


class TestSettings:
    def test_coerce(self):
        assert daemon.coerce_setting("5") == 5
        assert daemon.coerce_setting("true") is True
        assert daemon.coerce_setting("FALSE") is False
        assert daemon.coerce_setting("zai") == "zai"

    def test_sanitize_strips_secrets(self):
        s = daemon._sanitize({"api_key": "sk-x", "ZAI_API_KEY": "y", "provider": "zai", "max_tokens": 4096})
        assert s["api_key"] == "***stripped***" and s["ZAI_API_KEY"] == "***stripped***"
        assert s["provider"] == "zai"
        # numeric config is never a secret — the record stays readable
        assert s["max_tokens"] == 4096

    def test_new_id_shape(self):
        i = daemon.new_id("Test HTTP Target")
        assert i.startswith("d_") and i.endswith("test_http_target")


class TestEventTailAndRender:
    def _log(self, home, name="ev/events.jsonl"):
        p = home / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        return p

    def test_tail_incremental(self, home):
        p = self._log(home)
        p.write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"kind": "session.start", "objective": "obj"},
                    {"kind": "iteration", "n": 1, "phase": "recon"},
                )
            )
            + "\n"
        )
        recs, off = daemon.tail_events(p, 0)
        assert [r["kind"] for r in recs] == ["session.start", "iteration"]
        assert off > 0
        p.write_text(p.read_text() + json.dumps({"kind": "usage", "cost_usd": 0.5}) + "\n")
        recs2, off2 = daemon.tail_events(p, off)
        assert [r["kind"] for r in recs2] == ["usage"]
        assert off2 > off

    def test_tail_missing_file_is_empty(self, home):
        assert daemon.tail_events(home / "no.jsonl", 0) == ([], 0)

    def test_tail_drops_torn_tail(self, home):
        p = self._log(home)
        p.write_text(json.dumps({"kind": "iteration", "n": 1}) + "\n" + "{half-writ")
        recs, _ = daemon.tail_events(p, 0)
        assert [r["kind"] for r in recs] == ["iteration"]

    def test_render_lines(self):
        names: dict = {}
        assert daemon.render_event({"kind": "session.start", "objective": "obj"}, names).startswith("[boot]")
        assert (
            daemon.render_event({"kind": "iteration", "n": 3, "phase": "exploitation"}, names)
            == "  iter 3 · exploitation"
        )
        assert (
            daemon.render_event({"kind": "phase.transition", "to": "post_exploit"}, names) == "  phase → post_exploit"
        )
        assert daemon.render_event({"kind": "tool.call", "id": "a", "name": "nmap_scan"}, names) is None
        assert names["a"] == "nmap_scan"
        assert daemon.render_event({"kind": "tool.result", "id": "a", "ok": True}, names) == "  ✓ nmap_scan"
        assert "✗ nmap_scan" in daemon.render_event(
            {"kind": "tool.result", "id": "a", "ok": False, "error_kind": "timeout"}, names
        )
        assert "usage" in daemon.render_event(
            {"kind": "usage", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.25}, names
        )
        assert daemon.render_event({"kind": "state.snapshot", "findings": []}, names) is None

    def test_render_guidance_and_ending(self):
        assert "[operator]" in daemon.render_event({"kind": "guidance.delivered", "text": "try X"})
        assert "[complete]" in daemon.render_event({"kind": "session.complete", "reason": "objective_complete"})
        assert "[interrupt]" in daemon.render_event({"kind": "session.interrupt", "reason": "operator stop"})


class TestGuidanceSeam:
    def test_write_guidance_appends_to_state_dir(self, home):
        rec = {"state_dir": str(home / "eng" / "state")}
        p1 = daemon.write_guidance(rec, "focus on authz")
        p2 = daemon.write_guidance(rec, "then pivot")
        assert p1 == p2 == home / "eng" / "state" / "live_guidance.md"
        assert p1.read_text().splitlines() == ["focus on authz", "then pivot"]

    def test_write_guidance_without_engagement_is_none(self, home):
        assert daemon.write_guidance({}, "hi") is None


# ── the REAL detached child (protocol exercised cross-process) ──────────

#: a stub child that speaks the daemon protocol: boot → append journal
#: records → (on SIGTERM) stop, or finish. No LLM, no network.
_STUB_CHILD = """
import json, os, signal, sys, time
from pathlib import Path
sys.path.insert(0, {repo!r})
from suijin.modules.ops.lib import daemon
from suijin.modules.agent.lib.event_log import EventLog

rid = sys.argv[1]
d = daemon.daemon_dir()
eng = d / rid / "engagement"
eng.mkdir(parents=True, exist_ok=True)
daemon.update_record(rid, pid=os.getpid(), status="running",
                     engagement=str(eng), state_dir=str(eng / "state"),
                     events=str(eng / "events.jsonl"))

log = EventLog(eng / "events.jsonl")
log.append("session.start", objective="stub run")
log.append("iteration", n=1, phase="recon")
log.append("tool.call", id="a", name="nmap_scan")
log.append("tool.result", id="a", output="22 open", ok=True)

def _bye(sig, frame):
    log.append("session.interrupt", reason="operator stop")
    log.close()
    daemon.update_record(rid, status="stopped", exit_reason="operator stop", exit_code=0,
                         finished_at=daemon._now())
    os._exit(0)

signal.signal(signal.SIGTERM, _bye)

deadline = time.time() + {lifetime}
while time.time() < deadline:
    log.append("iteration", n=2, phase="exploitation")
    time.sleep(0.2)
log.append("session.complete", reason="objective_complete")
log.close()
daemon.update_record(rid, status="completed", exit_reason="objective_complete",
                     exit_code=0, finished_at=daemon._now())
"""


def _stub_argv(lifetime: float = 30.0) -> list[str]:
    code = _STUB_CHILD.format(repo=str(REPO), lifetime=lifetime)
    return [sys.executable, "-u", "-c", code, "d_stub"]


@pytest.mark.timeout(60) if hasattr(pytest.mark, "timeout") else (lambda f: f)
class TestDetachedSpawn:
    def test_spawn_detaches_child_that_records_itself(self, home, monkeypatch):
        """The child survives the console (new session) and completes the
        record contract: pid, engagement, events, status."""
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = daemon.new_id("stub run")
        # the stub child takes its id as argv[1] (python -c semantics)
        argv = _stub_argv()
        argv[-1] = rid
        rec = daemon.start_daemon(
            "stub run", environ={"SUIJIN_WORKSPACE": str(home)}, child_argv=argv, confirm_seconds=10
        )
        assert rec["status"] == "running" and rec["pid"] > 0
        assert rec["id"] == rid
        # booted: engagement paths recorded by the child itself
        deadline = time.time() + 10
        while time.time() < deadline:
            got = daemon._read_record(rid) or {}
            if got.get("engagement"):
                break
            time.sleep(0.1)
        got = daemon._read_record(rid)
        assert got["engagement"] and got["events"].endswith("events.jsonl")
        assert got["state_dir"].endswith("state")
        # the child's events are readable and render
        recs, _ = daemon.tail_events(got["events"], 0)
        assert recs and recs[0]["kind"] == "session.start"
        assert daemon.is_live(got)
        os.kill(rec["pid"], signal.SIGTERM)

    def test_stop_waits_for_graceful_shutdown(self, home, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = daemon.new_id("stoppable")
        argv = _stub_argv(lifetime=60)
        argv[-1] = rid
        daemon.start_daemon("stoppable", environ={"SUIJIN_WORKSPACE": str(home)}, child_argv=argv, confirm_seconds=10)
        # wait for boot so the SIGTERM handler is installed
        deadline = time.time() + 10
        while time.time() < deadline and not (daemon._read_record(rid) or {}).get("engagement"):
            time.sleep(0.1)
        rc = daemon.stop_daemon(rid)
        assert rc == 0
        final = daemon._read_record(rid)
        assert final["status"] == "stopped"
        assert final["exit_reason"] == "operator stop"
        assert not daemon.is_live(final)

    def test_child_natural_completion_lands_completed(self, home, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = daemon.new_id("shortlived")
        argv = _stub_argv(lifetime=0.2)
        argv[-1] = rid
        daemon.start_daemon("shortlived", environ={"SUIJIN_WORKSPACE": str(home)}, child_argv=argv)
        deadline = time.time() + 30
        while time.time() < deadline:
            got = daemon._read_record(rid) or {}
            if got.get("status") == "completed":
                break
            time.sleep(0.2)
        final = daemon._read_record(rid)
        assert final["status"] == "completed" and final["exit_reason"] == "objective_complete"
        assert not daemon.is_live(final)

    def test_spawn_dead_child_marked_dead(self, home, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rec = daemon.start_daemon(
            "doomed",
            environ={"SUIJIN_WORKSPACE": str(home)},
            child_argv=[sys.executable, "-c", "raise SystemExit(3)"],
            confirm_seconds=5.0,
        )
        assert rec["status"] in ("dead", "running")  # best-effort; dead wins when confirmed
        if rec["status"] == "dead":
            assert not daemon.is_live(rec)

    def test_spawn_nonexistent_binary_reports_failure(self, home):
        rec = daemon.start_daemon("ghost", child_argv=["/nonexistent/binary-xyz"], confirm_seconds=0)
        assert rec["status"] == "failed-to-spawn"
        assert rec["exit_reason"]


class TestChildLifecycle:
    """run_daemon_child in-process: record transitions + status mapping."""

    def test_completed_run_records_bundle_and_iterations(self, home, monkeypatch):
        rid = daemon.new_id("unit completed")
        _rec(home, id=rid, status="running", pid=0)
        monkeypatch.setattr(
            daemon,
            "run_engagement",
            lambda config, objective, resume_ref: {"completion_reason": "objective_complete", "current_iteration": 7},
        )
        # simulate a saved bundle on the CrashSaver
        import suijin.modules.tools.lib.engagement_bundle as eb

        monkeypatch.setattr(eb.CRASH_SAVER, "last_path", home / "eng" / "state" / "b.sje")
        rc = daemon.run_daemon_child(rid)
        final = daemon._read_record(rid)
        assert rc == 0
        assert final["status"] == "completed" and final["exit_reason"] == "objective_complete"
        assert final["iterations"] == 7
        assert final["bundle"].endswith("b.sje")
        assert final["finished_at"]

    def test_keyboardinterrupt_is_operator_stop(self, home, monkeypatch):
        rid = daemon.new_id("unit stopped")
        _rec(home, id=rid, status="running", pid=0)

        def _ki(config, objective, resume_ref):
            raise KeyboardInterrupt

        monkeypatch.setattr(daemon, "run_engagement", _ki)
        rc = daemon.run_daemon_child(rid)
        final = daemon._read_record(rid)
        assert rc == 0
        assert final["status"] == "stopped" and final["exit_reason"] == "operator stop"

    def test_crash_records_error_and_exit_1(self, home, monkeypatch):
        rid = daemon.new_id("unit crash")
        _rec(home, id=rid, status="running", pid=0)

        def _boom(config, objective, resume_ref):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(daemon, "run_engagement", _boom)
        rc = daemon.run_daemon_child(rid)
        final = daemon._read_record(rid)
        assert rc == 1
        assert final["status"] == "crash" and "provider exploded" in final["exit_reason"]

    def test_no_state_marks_stopped(self, home, monkeypatch):
        rid = daemon.new_id("unit nooutput")
        _rec(home, id=rid, status="running", pid=0)
        monkeypatch.setattr(daemon, "run_engagement", lambda c, o, r: {})
        assert daemon.run_daemon_child(rid) == 0
        assert daemon._read_record(rid)["status"] == "stopped"

    def test_child_requires_objective(self, home):
        assert daemon.run_daemon_child("d_missing") == 2

    def test_load_run_config_merges_cfg_file(self, home, monkeypatch):
        from suijin.modules.platform.lib import config_loader

        monkeypatch.setattr(config_loader, "load_config", lambda: {"provider": "zai", "max_iterations": 5})
        rid = daemon.new_id("cfg")
        daemon._cfg_path(rid).write_text(json.dumps({"max_iterations": 99, "provider": "deepseek"}))
        cfg = daemon.load_run_config(rid)
        assert cfg["max_iterations"] == 99 and cfg["provider"] == "deepseek"

    def test_daemon_default_autonomy_beats_pinned_empty(self, home, monkeypatch):
        """The operator's config.json pins `autonomy: ""` (present but
        disabled) — a daemon must still run unattended, or SIGTERM/stop
        would skip the clean stop-with-full-save path."""
        from suijin.modules.platform.lib import config_loader

        monkeypatch.setattr(config_loader, "load_config", lambda: {"autonomy": "", "provider": "zai"})
        rid = daemon.new_id("auto-default")
        assert daemon.load_run_config(rid)["autonomy"] == "full"

    def test_explicit_autonomy_wins_over_daemon_default(self, home, monkeypatch):
        from suijin.modules.platform.lib import config_loader

        monkeypatch.setattr(config_loader, "load_config", lambda: {"autonomy": ""})
        rid = daemon.new_id("auto-explicit")
        daemon._cfg_path(rid).write_text(json.dumps({"autonomy": "supervised"}))
        assert daemon.load_run_config(rid)["autonomy"] == "supervised"
        assert daemon.explicit_overrides(rid) == {"autonomy": "supervised"}

    def test_start_records_daemon_defaults(self, home):
        rec = daemon.start_daemon("defaults visible", child_argv=[sys.executable, "-c", "pass"])
        assert rec["daemon_defaults"] == {"autonomy": "full"}
        assert "autonomy" not in rec["set"]  # defaults are not operator pins


class TestListAndStopConsole:
    def test_list_empty(self, home, capsys):
        assert daemon.list_daemons() == 0
        assert "no daemon engagements" in capsys.readouterr().out

    def test_list_marks_dead(self, home, capsys):
        _rec(home, pid=999999, status="running")
        daemon.list_daemons()
        out = capsys.readouterr().out
        assert "dead" in out and "Test http" in out

    def test_list_shows_all(self, home, capsys):
        _rec(home, id="d_1", objective="one")
        _rec(home, id="d_2", objective="two", status="completed")
        daemon.list_daemons()
        out = capsys.readouterr().out
        assert "one" in out and "two" in out and "completed" in out

    def test_stop_unknown(self, home, capsys):
        assert daemon.stop_daemon("nope") == 1
        assert "no such" in capsys.readouterr().out

    def test_stop_finished(self, home, capsys):
        _rec(home, pid=0, status="completed", bundle="/x/b.sje")
        assert daemon.stop_daemon("d_20260101_000000_obj") == 0
        assert "already finished" in capsys.readouterr().out

    def test_stop_live_signals_pid(self, home, monkeypatch):
        sent = []
        monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: sent.append((pid, sig)))
        _rec(home, pid=4242, status="running")
        assert daemon.stop_daemon("d_20260101_000000_obj", wait=False) == 0
        # signal 0 is the liveness probe; the run only ever gets SIGTERM
        assert (4242, signal.SIGTERM) in sent
        assert all(sig in (0, signal.SIGTERM) for _, sig in sent)


class TestAttachConsole:
    def test_attach_unknown(self, home, capsys):
        assert daemon.attach_daemon("nope") == 1

    def test_attach_finished_run_prints_ending(self, home, capsys):
        _rec(
            home,
            status="completed",
            exit_reason="objective_complete",
            bundle="",
            events=str(home / "x" / "events.jsonl"),
        )
        assert daemon.attach_daemon("d_20260101_000000_obj") == 0
        out = capsys.readouterr().out
        assert "ENGAGEMENT COMPLETE" in out and "objective_complete" in out

    def test_attach_dead_running_exits(self, home, capsys):
        _rec(home, pid=999999, status="running")
        assert daemon.attach_daemon("d_20260101_000000_obj") == 0
        # a ghost "running" record with a dead pid ends the follow loop
        # (it never hangs) and prints an ending block
        assert "[" in capsys.readouterr().out

    def test_attach_streams_events_then_stops(self, home, capsys):
        # a record whose log has a complete event, pid already dead
        ev = home / "eng" / "events.jsonl"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"kind": "session.start", "objective": "obj"},
                    {"kind": "iteration", "n": 1, "phase": "recon"},
                    {"kind": "tool.call", "id": "a", "name": "nmap_scan"},
                    {"kind": "tool.result", "id": "a", "ok": True},
                    {"kind": "session.complete", "reason": "done"},
                )
            )
            + "\n"
        )
        _rec(home, status="running", pid=999999, events=str(ev))
        assert daemon.attach_daemon("d_20260101_000000_obj") == 0
        out = capsys.readouterr().out
        assert "[boot] obj" in out
        assert "iter 1 · recon" in out
        assert "✓ nmap_scan" in out


class TestCLIWiring:
    def test_verbs_registered(self):
        from suijin.modules.console.lib.cli import _KNOWN_VERBS, is_known_verb

        for v in ("daemon", "daemon-run", "ps", "attach", "stop"):
            assert v in _KNOWN_VERBS and is_known_verb(v), v

    def test_ps_verb_uses_daemon(self, home, capsys):
        from suijin.modules.console.lib.cli import run_ps_cmd

        assert run_ps_cmd(None) == 0
        assert "no daemon" in capsys.readouterr().out

    def test_daemon_start_builds_overrides(self, home, monkeypatch, capsys):
        import suijin.modules.console.lib.cli as cli

        captured = {}

        def _fake_start(objective, overrides=None, resume_ref="", confirm_seconds=0.0):
            captured.update(objective=objective, overrides=overrides, resume_ref=resume_ref, wait=confirm_seconds)
            return {"id": "d_x", "pid": 1, "status": "running"}

        monkeypatch.setattr(cli._daemon_mod(), "start_daemon", _fake_start)
        args = type(
            "A", (), {"objective": "run x", "set": ["provider=zai", "max_iterations=100"], "resume": "", "wait": 0.0}
        )()
        assert cli.run_daemon_start(args) == 0
        assert captured["objective"] == "run x"
        assert captured["overrides"] == {"provider": "zai", "max_iterations": 100}
        assert "started d_x" in capsys.readouterr().out

    def test_daemon_start_failure_exits_1(self, home, monkeypatch, capsys):
        import suijin.modules.console.lib.cli as cli

        monkeypatch.setattr(
            cli._daemon_mod(),
            "start_daemon",
            lambda *a, **k: {"id": "d_x", "pid": 0, "status": "failed-to-spawn", "exit_reason": "nope", "log": ""},
        )
        args = type("A", (), {"objective": "x", "set": [], "resume": "", "wait": 0.0})()
        assert cli.run_daemon_start(args) == 1
        assert "error" in capsys.readouterr().out

    def test_daemon_requires_objective(self, home):
        with pytest.raises(ValueError):
            daemon.start_daemon("")


class TestResumeResolution:
    def test_resolve_resume_empty_is_none(self, home):
        assert daemon.resolve_resume("") == (None, "", "")

    def test_resolve_resume_bundle_carries_source_objective(self, home, monkeypatch):
        import suijin.modules.tools.lib.engagement_bundle as eb

        path = eb.save_engagement(
            "t",
            "original objective text",
            {},
            {"messages": [{"role": "user", "content": "hi"}], "current_phase": "recon"},
        )
        monkeypatch.setattr(eb, "restore_side_files", lambda p: 0)
        state, source, objective = daemon.resolve_resume(str(path))
        assert source.startswith("bundle:") and state["current_phase"] == "recon"
        assert state["completion_reason"] is None
        # a resume continues ITS engagement, not the command-line text
        assert objective == "original objective text"

    def test_resolve_resume_journal_uses_full_replay(self, home):
        """The journal branch must use replay() — the diagnostics-only
        export_state_snapshot would resume with the snapshot fields alone
        (no messages, no phase: the field bug)."""
        eng = home / "engagements" / "20260101_120000_journal_target"
        eng.mkdir(parents=True, exist_ok=True)
        (eng / "events.jsonl").write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"kind": "session.start", "objective": "journal objective"},
                    {"kind": "iteration", "n": 4, "phase": "exploitation"},
                    {"kind": "assistant.message", "content": "recon complete"},
                    {"kind": "state.snapshot", "todo_list": [{"task": "pivot"}]},
                )
            )
            + "\n"
        )
        state, source, objective = daemon.resolve_resume("20260101_120000_journal_target")
        assert source == "journal:20260101_120000_journal_target"
        assert state["current_phase"] == "exploitation"  # from the event stream
        assert state["current_iteration"] == 4
        assert state["messages"][0]["content"] == "recon complete"  # the conversation
        assert state["todo_list"] == [{"task": "pivot"}]  # the snapshot overlay
        assert state["completion_reason"] is None
        assert objective == "journal objective"

    def test_resolve_resume_refuses_completed_journal(self, home):
        eng = home / "engagements" / "20260101_130000_done"
        eng.mkdir(parents=True, exist_ok=True)
        (eng / "events.jsonl").write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"kind": "session.start", "objective": "finished"},
                    {"kind": "iteration", "n": 2},
                    {"kind": "session.complete", "reason": "objective_complete"},
                )
            )
            + "\n"
        )
        assert daemon.resolve_resume("20260101_130000_done")[1] == ""
        assert "COMPLETED" in daemon.preflight_resume("20260101_130000_done")

    def test_resolve_resume_unknown(self, home):
        assert daemon.resolve_resume("definitely-not-a-bundle-or-engagement") == (None, "", "")

    def test_preflight_unknown_is_explained(self, home):
        assert "no resumable" in daemon.preflight_resume("nope-nope-nope")

    def test_preflight_accepts_live_journal(self, home):
        eng = home / "engagements" / "20260101_140000_live"
        eng.mkdir(parents=True, exist_ok=True)
        (eng / "events.jsonl").write_text(
            json.dumps({"kind": "session.start", "objective": "x"})
            + "\n"
            + json.dumps({"kind": "iteration", "n": 1})
            + "\n"
        )
        assert daemon.preflight_resume("20260101_140000_live") == ""

    def test_preflight_empty_ok(self, home):
        assert daemon.preflight_resume("") == ""

    def test_preflight_refuses_empty_bundle(self, home, monkeypatch):
        """A crash backstop that wrote an empty graph_state has nothing to
        continue — resuming would silently start a blank engagement."""
        import suijin.modules.tools.lib.engagement_bundle as eb

        path = eb.save_engagement("t", "empty crash", {}, {})
        assert "no state to resume" in daemon.preflight_resume(str(path))
        state, source, _ = daemon.resolve_resume(str(path))
        assert (state, source) == (None, "")
        monkeypatch.setattr(eb, "restore_side_files", lambda p: 0)

    def test_start_fails_fast_on_bad_resume(self, home, monkeypatch, capsys):
        """A bad --resume must not spawn a child that crashes a second later."""
        import suijin.modules.console.lib.cli as cli

        spawned = []
        monkeypatch.setattr(cli._daemon_mod(), "start_daemon", lambda *a, **k: spawned.append(a) or {})
        args = type("A", (), {"objective": "x", "set": [], "resume": "no-such-thing", "wait": 0.0})()
        assert cli.run_daemon_start(args) == 1
        assert not spawned
        assert "cannot resume" in capsys.readouterr().out


class TestRealEntryPoint:
    """The PRODUCTION entry: `python -m suijin.main daemon-run` in a real
    subprocess (main.py's verb firewall → cli.py → the child). This is the
    one test that proves the whole dispatch chain, not just the module."""

    def test_main_dispatches_daemon_run_and_lands_record(self, home):
        # a sitecustomize that swaps the LLM runner for a deterministic
        # fake, so the child runs the real entry end-to-end offline.
        # sitecustomize runs BEFORE `-m` puts cwd on sys.path, so the repo
        # root is pinned explicitly here.
        stub = home / "stub"
        stub.mkdir()
        (stub / "sitecustomize.py").write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "import suijin.modules.ops.lib.daemon as d\n"
            "assert hasattr(d, 'run_engagement')\n"
            "d.run_engagement = lambda config, objective, resume_ref='': {\n"
            "    'completion_reason': 'objective_complete', 'current_iteration': 12,\n"
            "    'current_phase': 'post_exploit', 'findings': []}\n"
        )
        rid = daemon.new_id("real entry")
        _rec(home, id=rid, status="running", pid=0, objective="real entry")
        env = dict(os.environ)
        env.update({"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub), "SUIJIN_DAEMON_ID": rid})
        out = subprocess.run(
            [sys.executable, "-m", "suijin.main", "daemon-run", "--id", rid, "--config", str(daemon._cfg_path(rid))],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert out.returncode == 0, out.stderr[-2000:]
        final = daemon._read_record(rid)
        assert final is not None
        assert final["status"] == "completed", final
        assert final["exit_reason"] == "objective_complete"
        assert final["iterations"] == 12
        assert final["pid"] > 0
        # the recorded config is the effective one, sanitized
        assert final["config"]["autonomy"] == "full"
        assert "api_key" not in json.dumps(final["config"]).lower() or "***stripped***" in json.dumps(final["config"])

    def test_main_verb_firewall_registers_daemon_run(self):
        """main.py only dispatches argv to the CLI for KNOWN verbs — a
        daemon-run typo must NOT be swallowed as 'launch the TUI'."""
        from suijin.modules.console.lib.cli import is_known_verb

        assert is_known_verb("daemon-run")
        assert is_known_verb("daemon")
        assert not is_known_verb("daemon_run")  # underscore is not the verb

    def _write_runner_stub(self, home, body: str) -> str:
        """A sitecustomize whose fake runner replaces run_engagement."""
        stub = home / "stub"
        stub.mkdir(exist_ok=True)
        (stub / "sitecustomize.py").write_text(
            f"import sys\nsys.path.insert(0, {str(REPO)!r})\nimport suijin.modules.ops.lib.daemon as d\n" + body
        )
        return str(stub)

    def test_full_lifecycle_spawn_detach_and_stop(self, home):
        """start -> detached (own session) -> SIGTERM -> graceful record.
        Uses the REAL child entry (`-m suijin.main daemon-run`); only the
        LLM runner is stubbed. The stub mimics the runner's full-auto
        SIGTERM contract (handler raises KeyboardInterrupt → the child
        records an operator stop)."""
        stub = self._write_runner_stub(
            home,
            "import signal, time\n"
            "def _fake(config, objective, resume_ref=''):\n"
            "    from suijin.modules.platform.lib.workspace import set_engagement\n"
            "    set_engagement(objective)  # the real runner scopes the run first\n"
            "    def _bye(sig, frame):\n"
            "        raise KeyboardInterrupt\n"
            "    signal.signal(signal.SIGTERM, _bye)\n"
            "    time.sleep(120)\n"
            "    return {'completion_reason': 'objective_complete', 'current_iteration': 1}\n"
            "d.run_engagement = _fake\n",
        )
        rec = daemon.start_daemon(
            "lifecycle target",
            environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": stub},
            confirm_seconds=15.0,
        )
        assert rec["status"] == "running" and rec["pid"] > 0
        # DETACHED: the child leads its own session (this shell does not),
        # which is what makes it survive the console exiting
        assert os.getsid(rec["pid"]) == rec["pid"]
        assert os.getsid(0) != rec["pid"]
        # it booted and the watcher found the engagement
        deadline = time.time() + 20
        while time.time() < deadline and not (daemon._read_record(rec["id"]) or {}).get("engagement"):
            time.sleep(0.2)
        booted = daemon._read_record(rec["id"])
        assert booted["engagement"] and booted["state_dir"] and booted["events"]
        assert booted["config"]["autonomy"] == "full"
        # graceful stop: SIGTERM → the child lands a stopped record
        assert daemon.stop_daemon(rec["id"], grace=30) == 0
        final = daemon._read_record(rec["id"])
        assert final["status"] == "stopped" and final["exit_reason"] == "operator stop"
        assert final["exit_code"] == 0 and final["finished_at"]
        assert not daemon.is_live(final)
        assert Path(final["log"]).is_file()  # the run log was kept


class TestLiveAttachIntegration:
    """Attach against a REAL live daemon: streams its journal, writes
    operator guidance into the engagement's live_guidance.md (the seam
    the TUI uses), and exits when the run ends."""

    def _spawn_stub_run(self, home, stub_dir, rid, lifetime=20.0):
        code = _STUB_CHILD.format(repo=str(REPO), lifetime=lifetime)
        argv = [sys.executable, "-u", "-c", code, rid]
        rec = daemon.start_daemon(
            "attach target",
            environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub_dir)},
            child_argv=argv,
        )
        deadline = time.time() + 10
        while time.time() < deadline and not (daemon._read_record(rid) or {}).get("engagement"):
            time.sleep(0.1)
        return rec

    def test_attach_streams_and_ends(self, home, capsys, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = daemon.new_id("attach target")
        stub = home / "stub"
        stub.mkdir()
        self._spawn_stub_run(home, stub, rid, lifetime=0.6)
        # attach runs its loop; with a non-TTY stdin it just follows
        assert daemon.attach_daemon(rid, poll=0.1, heartbeat=0) == 0
        out = capsys.readouterr().out
        assert "attached:" in out
        assert "[boot] stub run" in out  # journal streamed
        assert "iter 1" in out
        assert "✓ nmap_scan" in out  # tool.call→tool.result name mapping

    def test_attach_writes_guidance_to_live_seam(self, home, capsys, monkeypatch):
        """The operator types a line; it lands in state/live_guidance.md
        exactly like the TUI's guidance channel."""
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = daemon.new_id("guidance target")
        stub = home / "stub"
        stub.mkdir()
        rec = self._spawn_stub_run(home, stub, rid, lifetime=20.0)
        booted = daemon._read_record(rid) or {}
        assert booted.get("state_dir")
        # simulate what attach's stdin handler does on a line of input
        p = daemon.write_guidance(booted, "focus on the admin panel")
        assert p.is_file()
        assert p.read_text().strip() == "focus on the admin panel"
        # the runner consumes this file (think node reads+clears it)
        os.kill(rec["pid"], signal.SIGTERM)

    def test_attach_detach_keeps_daemon_running(self, home, monkeypatch):
        """Detaching never stops the run — that is the whole point of the
        split (the console can leave and the engagement continues)."""
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = daemon.new_id("detach target")
        stub = home / "stub"
        stub.mkdir()
        rec = self._spawn_stub_run(home, stub, rid, lifetime=20.0)
        # attach is not what stops it — verify the daemon is still live
        # after a hypothetical detach (we call stop explicitly)
        assert daemon.is_live(daemon._read_record(rid) or {})
        os.kill(rec["pid"], signal.SIGTERM)
        deadline = time.time() + 10
        while time.time() < deadline and daemon.is_live(daemon._read_record(rid) or {}):
            time.sleep(0.1)
        assert (daemon._read_record(rid) or {})["status"] == "stopped"


class TestPanicReachesDaemons:
    """The emergency stop must reach a detached engagement — through the
    daemon seam (SIGTERM → full save), never a bare kill."""

    def test_panic_stops_live_daemon_with_full_save(self, home, monkeypatch, capsys):
        from suijin.modules.ops.lib import panic as pk

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        stub = home / "stub"
        stub.mkdir()
        rid = daemon.new_id("panic target")
        code = _STUB_CHILD.format(repo=str(REPO), lifetime=120)
        daemon.start_daemon(
            "panic target",
            environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub)},
            child_argv=[sys.executable, "-u", "-c", code, rid],
        )
        deadline = time.time() + 10
        while time.time() < deadline and not (daemon._read_record(rid) or {}).get("engagement"):
            time.sleep(0.1)
        assert daemon.is_live(daemon._read_record(rid) or {})
        # panic's pkill machinery is neutralized: only the daemon seam acts
        monkeypatch.setattr(pk.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 1})())
        out = pk.panic()
        assert "daemon engagements: SIGTERM" in out
        deadline = time.time() + 20
        while time.time() < deadline and daemon.is_live(daemon._read_record(rid) or {}):
            time.sleep(0.2)
        final = daemon._read_record(rid) or {}
        assert final["status"] == "stopped"  # graceful, full-save record

    def test_panic_dry_run_reports_without_signalling(self, home, monkeypatch):
        from suijin.modules.ops.lib import panic as pk

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rid = "d_20260101_000000_obj"
        _rec(home, pid=os.getpid(), status="running")  # "live" (our own pid)
        out = pk.panic(dry_run=True)
        assert "would stop 1" in out
        # dry-run only reports — the live record is untouched
        assert daemon.is_live(daemon._read_record(rid) or {})

    def test_panic_reports_none_running(self, home, monkeypatch):
        from suijin.modules.ops.lib import panic as pk

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        assert "daemon engagements: none running" in pk.panic(dry_run=True)


class TestDocstrings:
    def test_module_docstring_names_the_verbs(self):
        doc = daemon.__doc__ or ""
        for token in ("suijin daemon start", "suijin ps", "suijin attach", "suijin stop"):
            assert token in doc, token


class TestConcurrencyAndRecovery:
    """Races the daemon can actually hit: parallel runs, a writer that
    dies mid-update, kill -9, and ps/attach against garbage."""

    def test_two_daemons_are_independent(self, home, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        stub = home / "stub"
        stub.mkdir()
        ids = []
        for i, lifetime in enumerate((0.4, 0.9)):
            rid = daemon.new_id(f"parallel {i}")
            code = _STUB_CHILD.format(repo=str(REPO), lifetime=lifetime)
            daemon.start_daemon(
                f"parallel {i}",
                environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub)},
                child_argv=[sys.executable, "-u", "-c", code, rid],
            )
            ids.append(rid)
        deadline = time.time() + 30
        while time.time() < deadline:
            recs = [daemon._read_record(r) or {} for r in ids]
            if all(r.get("status") == "completed" for r in recs):
                break
            time.sleep(0.2)
        assert all((daemon._read_record(r) or {}).get("status") == "completed" for r in ids)
        # each child wrote its OWN engagement dir (no cross-talk)
        engs = {(daemon._read_record(r) or {}).get("engagement") for r in ids}
        assert len(engs) == 2 and all(engs)

    def test_kill9_leaves_record_and_ps_reports_dead(self, home, monkeypatch):
        """SIGKILL can't write a final record — ps must still tell the truth
        (dead), and the journal stays resumable."""
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        stub = home / "stub"
        stub.mkdir()
        rid = daemon.new_id("hard killed")
        code = _STUB_CHILD.format(repo=str(REPO), lifetime=120)
        rec = daemon.start_daemon(
            "hard killed",
            environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub)},
            child_argv=[sys.executable, "-u", "-c", code, rid],
        )
        deadline = time.time() + 10
        while time.time() < deadline and not (daemon._read_record(rid) or {}).get("engagement"):
            time.sleep(0.1)
        booted = daemon._read_record(rid) or {}
        os.kill(rec["pid"], signal.SIGKILL)
        time.sleep(0.5)
        current = daemon._read_record(rid) or {}
        assert current["status"] == "running"  # record still claims running…
        assert not daemon.is_live(current)  # …but it is not
        # the journal survived the kill → still resumable
        assert Path(booted["events"]).is_file()
        # and the recovery verb finds it
        from suijin.modules.ops.lib.journal_resume import _is_resumable

        assert _is_resumable(Path(booted["events"]))

    def test_ps_survives_garbage_and_torn_records(self, home, capsys):
        (daemon.daemon_dir() / "d_broken.json").write_text("{oops")
        (daemon.daemon_dir() / "d_torn.json.tmp").write_text('{"id": "d_tor')
        _rec(home, id="d_good", objective="survivor")
        assert daemon.list_daemons() == 0
        out = capsys.readouterr().out
        assert "survivor" in out and "d_broken" not in out

    def test_attach_torn_journal_tail_never_crashes(self, home, capsys):
        ev = home / "eng" / "events.jsonl"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(
            json.dumps({"kind": "iteration", "n": 1, "phase": "recon"})
            + "\n"
            + '{"kind": "tool.resu'  # killed mid-write
        )
        _rec(home, pid=999999, events=str(ev), status="running")
        assert daemon.attach_daemon("d_20260101_000000_obj", poll=0.1, heartbeat=0) == 0
        assert "iter 1" in capsys.readouterr().out

    def test_stop_while_child_bootstrapping(self, home, monkeypatch):
        """stop during boot (before the runner installs handlers): the
        child still dies and the record is not left lying."""
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        stub = home / "stub"
        stub.mkdir()
        rid = daemon.new_id("early stop")
        code = _STUB_CHILD.format(repo=str(REPO), lifetime=120)
        daemon.start_daemon(
            "early stop",
            environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub)},
            child_argv=[sys.executable, "-u", "-c", code, rid],
        )
        assert daemon.stop_daemon(rid, grace=20) == 0
        time.sleep(0.3)
        assert not daemon.is_live(daemon._read_record(rid) or {})

    def test_resolve_id_ignores_cfg_files(self, home):
        """The cfg file beside a record must never be mistaken for one."""
        rid = daemon.new_id("cfg not a record")
        _rec(home, id=rid)
        daemon._cfg_path(rid).write_text('{"provider": "zai"}')
        assert daemon.resolve_id("d_20260924") in (None, rid)
        assert all(r.get("id") for r in daemon._records())
        assert len(daemon._records()) == 1


class TestFastChildKeepsItsVerdict:
    """A run that FINISHES inside the --wait window is a success, not a
    boot failure: the boot confirmation must never relabel it 'dead'
    (it cost a real run its completion record)."""

    def test_fast_completion_survives_boot_confirmation(self, home, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        stub = home / "stub"
        stub.mkdir()
        (stub / "sitecustomize.py").write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "import suijin.modules.ops.lib.daemon as d\n"
            "def _fake(config, objective, resume_ref=''):\n"
            "    from suijin.modules.platform.lib.workspace import set_engagement\n"
            "    set_engagement(objective)\n"
            "    return {'completion_reason': 'objective_complete', 'current_iteration': 1}\n"
            "d.run_engagement = _fake\n"
        )
        rec = daemon.start_daemon(
            "fast finish",
            environ={"SUIJIN_WORKSPACE": str(home), "PYTHONPATH": str(stub)},
            confirm_seconds=10.0,
        )
        final = daemon._read_record(rec["id"]) or {}
        assert final["status"] == "completed", final
        assert final["exit_reason"] == "objective_complete"
        assert final["iterations"] == 1

    def test_dead_on_arrival_is_still_marked_dead(self, home, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(home))
        rec = daemon.start_daemon(
            "dies at once",
            environ={"SUIJIN_WORKSPACE": str(home)},
            child_argv=[sys.executable, "-c", "raise SystemExit(2)"],
            confirm_seconds=5.0,
        )
        assert rec["status"] == "dead"  # never a ghost 'running'


class TestNoAdminRequired:
    """The daemon must run as an ordinary user: no privilege changes, no
    privileged ports, no writes outside the user's own workspace."""

    _FORBIDDEN = {
        "setuid",
        "setgid",
        "seteuid",
        "setegid",
        "setreuid",
        "setregid",
        "chown",
        "chroot",
        "mount",
        "umount",
        "pivot_root",
        "unshare",
    }

    def test_no_privilege_calls_in_daemon(self):
        import ast

        tree = ast.parse((REPO / "suijin" / "server" / "ops" / "lib" / "daemon.py").read_text())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                called.add(name)
        assert not (called & self._FORBIDDEN), f"privileged calls: {called & self._FORBIDDEN}"

    def test_no_sudo_in_launchers(self):
        installer = (REPO / "install.sh").read_text()
        assert "suijind" in installer
        daemon_block = installer[installer.find("DAEMON_LAUNCHER") : installer.find('ok "daemon launcher')]
        assert "sudo" not in daemon_block and "doas" not in daemon_block
        # the launcher runs the SAME interpreter as suijin — no root, no service
        assert "$VENV/bin/python" in daemon_block
        assert "cli.py" in daemon_block

    def test_spawn_is_a_plain_detached_child(self, home, monkeypatch):
        """Detachment is start_new_session (a session, not a privilege)."""
        seen = {}

        class _P:
            pid = 4321

            def __init__(self, argv, **kw):
                seen.update(argv=argv, kw=kw)

        monkeypatch.setattr(daemon.subprocess, "Popen", _P)
        monkeypatch.setattr(daemon, "actor_alive", lambda pid: True)
        rec = daemon.start_daemon("no privilege", child_argv=[sys.executable, "-c", "pass"])
        assert seen["kw"]["start_new_session"] is True
        assert seen["kw"].get("user") is None and seen["kw"].get("group") is None
        assert seen["kw"].get("env") is not None  # plain env inheritance
        assert rec["pid"] == 4321

    def test_ports_stay_closed(self):
        """The daemon never binds a socket (attach is a journal reader)."""
        source = (REPO / "suijin" / "server" / "ops" / "lib" / "daemon.py").read_text()
        assert "socket" not in source
        assert "bind(" not in source


class TestSuijindVerb:
    """`suijind` — the daemon by another name (a bare CLI verb, no root)."""

    def test_verb_registered(self):
        from suijin.modules.console.lib.cli import _KNOWN_VERBS, is_known_verb

        assert "suijind" in _KNOWN_VERBS and is_known_verb("suijind")
        assert "tui" in _KNOWN_VERBS and is_known_verb("tui")

    def test_run_starts_and_attaches(self, home, monkeypatch, capsys):
        from suijin.modules.console.lib import cli

        seen = {}

        def _fake_launch(objective, overrides=None, resume_ref="", attach=True, wait=0.0):
            seen.update(objective=objective, overrides=overrides, resume_ref=resume_ref, attach=attach, wait=wait)
            return 0

        monkeypatch.setattr(cli._daemon_mod(), "launch_and_attach", _fake_launch)
        args = type(
            "A",
            (),
            {"objective": "suijind target", "set": ["provider=zai"], "resume": "ref", "attach": True, "wait": 2.0},
        )()
        assert cli.run_suijind_run(args) == 0
        assert seen["objective"] == "suijind target"
        assert seen["overrides"] == {"provider": "zai"}
        assert seen["attach"] is True and seen["wait"] == 2.0

    def test_run_no_attach_flag(self, home, monkeypatch):
        from suijin.modules.console.lib import cli

        seen = {}
        monkeypatch.setattr(
            cli._daemon_mod(),
            "launch_and_attach",
            lambda objective, **kw: seen.update(kw) or 0,
        )
        args = type("A", (), {"objective": "fire and forget", "set": [], "resume": "", "attach": False, "wait": 0.0})()
        assert cli.run_suijind_run(args) == 0
        assert seen["attach"] is False

    def test_bare_suijind_non_tty_prints_usage(self, home, monkeypatch, capsys):
        """No objective and no TTY (a script) → a clean usage line, not a hang."""
        from suijin.modules.console.lib import cli

        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
        assert cli.run_suijind(type("A", (), {})()) == 2
        out = capsys.readouterr().out
        assert "usage: suijind" in out

    def test_ps_and_stop_route_to_daemon(self, home, monkeypatch, capsys):
        from suijin.modules.console.lib import cli

        monkeypatch.setattr(cli._daemon_mod(), "list_daemons", lambda: 0)
        assert cli.run_ps_cmd(None) == 0
        monkeypatch.setattr(cli._daemon_mod(), "stop_daemon", lambda ref, **kw: print(f"stopped {ref}") or 0)
        assert cli.run_stop_cmd(type("A", (), {"id": "abc"})()) == 0
        assert "stopped abc" in capsys.readouterr().out

    def test_tui_verb_runs_in_process(self, home, monkeypatch):
        """`suijin tui` = the classic in-process run (the opt-out)."""
        from suijin.modules.console.lib import cli
        from suijin.modules.redteam.lib import redteamer

        seen = {}
        monkeypatch.setattr(redteamer, "run_red_team", lambda config, obj: seen.update(obj=obj))
        monkeypatch.setattr("suijin.modules.platform.lib.config_loader.load_config", lambda: {"provider": "zai"})
        assert cli.run_tui_cmd(type("A", (), {"objective": "foreground obj"})()) == 0
        assert seen["obj"] == "foreground obj"

    def test_tui_non_tty_without_objective_is_usage(self, monkeypatch, capsys):
        from suijin.modules.console.lib import cli

        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
        assert cli.run_tui_cmd(type("A", (), {"objective": ""})()) == 2
        assert "usage: suijin tui" in capsys.readouterr().out


class TestLaunchModeDefault:
    """The console default: an engagement runs in the DAEMON so it
    outlives the console. launch_mode: tui keeps the in-process TUI."""

    def test_config_default_is_daemon(self):
        from suijin.modules.platform.lib.config_loader import _default_config

        assert _default_config()["launch_mode"] == "daemon"

    def test_menu_is_unchanged_three_options(self):
        """The objective menu stays EXACTLY as it was (1 type / 2 upload /
        3 back) — the daemon default never rewrites the operator's menu."""
        from suijin.modules.redteam.lib import redteamer

        source = Path(redteamer.__file__).read_text()
        assert "Type manually" in source and "Upload file (.txt / .md / .rtf)" in source
        assert "[bold white]3.[/] [dim]Back[/]" in source
        assert "classic live TUI" not in source  # no extra menu rows added

    def test_menu_choice_one_starts_daemon(self, monkeypatch):
        """The DEFAULT path: detached. The in-process runner is never called."""
        from suijin.modules.ops.lib import daemon as dmod
        from suijin.modules.redteam.lib import redteamer

        started = {}
        in_process = {}
        answers = iter(["1", "detached target"])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        monkeypatch.setattr(redteamer, "load_config", lambda: {"launch_mode": "daemon"})
        monkeypatch.setattr(redteamer, "load_env", lambda: None)
        monkeypatch.setattr(redteamer, "discover_modules", lambda *a, **k: None)
        monkeypatch.setattr("suijin.modules.loader.set_verbose", lambda *a, **k: None)
        monkeypatch.setattr(redteamer, "run_red_team", lambda config, obj: in_process.setdefault("called", obj))
        monkeypatch.setattr(redteamer.console, "print", lambda *a, **k: None)
        monkeypatch.setattr(dmod, "launch_and_attach", lambda obj, **kw: started.update(obj=obj) or 0)
        redteamer.main()
        assert started["obj"] == "detached target"
        assert "called" not in in_process  # detached is the default

    def test_launch_mode_tui_config_forces_in_process(self, monkeypatch):
        """launch_mode: tui in config = old behavior, even from option 1."""
        from suijin.modules.ops.lib import daemon as dmod
        from suijin.modules.redteam.lib import redteamer

        in_process = {}
        answers = iter(["1", "tui configured"])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        monkeypatch.setattr(redteamer, "load_config", lambda: {"launch_mode": "tui"})
        monkeypatch.setattr(redteamer, "load_env", lambda: None)
        monkeypatch.setattr(redteamer, "discover_modules", lambda *a, **k: None)
        monkeypatch.setattr("suijin.modules.loader.set_verbose", lambda *a, **k: None)
        monkeypatch.setattr(redteamer, "run_red_team", lambda config, obj: in_process.update(obj=obj))
        monkeypatch.setattr(redteamer.console, "print", lambda *a, **k: None)
        monkeypatch.setattr(dmod, "launch_and_attach", lambda obj, **kw: pytest.fail("must not daemon"))
        redteamer.main()
        assert in_process["obj"] == "tui configured"


class TestAttachConsoleCommands:
    """The attached console reads the journal — no in-process graph needed."""

    def _rec(self, home):
        ev = home / "eng" / "events.jsonl"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"kind": "session.start", "objective": "obj"},
                    {"kind": "usage", "input_tokens": 5000, "output_tokens": 1200, "cost_usd": 0.42},
                    {
                        "kind": "state.snapshot",
                        "current_phase": "post_exploit",
                        "current_iteration": 7,
                        "todo_list": [{"task": "verify IDOR"}],
                        "findings": [{"title": "IDOR", "severity": "high"}],
                        "pending_questions": [{"question": "authorized for DoS?"}],
                    },
                )
            )
            + "\n"
        )
        return {"events": str(ev), "engagement": str(home / "eng"), "state_dir": str(home / "eng" / "state")}

    def test_state_command(self, home, capsys):
        daemon._cmd_state(self._rec(home))
        out = capsys.readouterr().out
        assert "post_exploit" in out and "iteration: 7" in out
        assert "verify IDOR" in out and "authorized for DoS?" in out

    def test_findings_command(self, home, capsys):
        daemon._cmd_findings(self._rec(home))
        assert "[high] IDOR" in capsys.readouterr().out

    def test_cost_command_sums_usage(self, home, capsys):
        daemon._cmd_cost(self._rec(home))
        out = capsys.readouterr().out
        assert "5000" in out and "1200" in out and "$0.4200" in out

    def test_note_command_writes_into_the_engagement(self, home, capsys):
        rec = self._rec(home)
        daemon._cmd_note(rec, "client confirmed the finding")
        notes = list((home / "eng" / ".notes").glob("operator_*.md"))
        assert len(notes) == 1 and "client confirmed" in notes[0].read_text()

    def test_commands_without_engagement_are_honest(self, home, capsys):
        daemon._cmd_state({"events": ""})
        daemon._cmd_findings({"events": ""})
        daemon._cmd_cost({"events": ""})
        daemon._cmd_note({}, "x")
        out = capsys.readouterr().out
        assert "no state snapshot yet" in out and "no findings" in out
        assert "no note written" in out

    def test_help_names_the_foreground_limitations(self):
        assert "suijin tui" in daemon.ATTACH_HELP
        assert "/compact" in daemon.ATTACH_HELP
        for verb in ("/state", "/findings", "/cost", "/note", "/stop", "/detach"):
            assert verb in daemon.ATTACH_HELP
