"""The .sje bundle derived from the event journal (sje_export + save_engagement).

Contract: graph_state.json is REPLAYED from events.jsonl (THE truth), not
copied from a live frame — so the crash backstops (SIGTERM, excepthook,
atexit) get the same resume contract as `suijin resume`. The caller's live
frame only fills journal gaps. An empty/absent journal falls back to the
in-memory state (a crash before the first session.start).
"""

from __future__ import annotations

import json

import pytest

from suijin.modules.tools.lib import engagement_bundle as eb


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    import suijin.modules.platform.lib.workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._reset_engagement()  # hermetic: no engagement pinned from another test
    return tmp_path


def _events_path(tmp_path, n=1):
    p = tmp_path / f"engagements/e{n}/events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"kind": "session.start", "seq": 1, "objective": "Test http://target.example"},
        {"kind": "iteration", "seq": 2, "n": 3, "phase": "exploitation"},
        {"kind": "tool.call", "seq": 3, "id": "A", "name": "nmap_scan", "args": {"target": "x"}},
        {"kind": "tool.result", "seq": 4, "id": "A", "output": "22/tcp open", "ok": True},
        {"kind": "assistant.message", "seq": 5, "content": "recon done"},
        {"kind": "guidance.delivered", "seq": 6, "text": "try exploit-now"},
        {"kind": "state.snapshot", "seq": 7, "findings": [{"title": "KV"}]},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def _frame_state():
    """A live in-memory frame from a buggy/partial get_state() — the frame
    the crash paths USED to serialize (message not yet confirmed)."""
    return {
        "messages": [{"role": "assistant", "content": "MID-FLIGHT not confirmed"}],
        "conversation_objectives": ["go deeper"],
        "current_phase": "exploitation",
        "current_iteration": 0,
        "completion_reason": "llm_error_mid_turn",
    }


class TestDeriveGraphState:
    def test_replays_the_record(self, tmp_path):
        from suijin.modules.ops.lib import sje_export

        st = sje_export.derive_graph_state(_events_path(tmp_path))
        assert st["original_objective"] == "Test http://target.example"
        assert st["current_iteration"] == 3
        assert st["current_phase"] == "exploitation"
        # ordered conversation: assistant msg, then the guidance-as-last-message
        assert [m["role"] for m in st["messages"]] == ["assistant", "user"]
        assert st["execution_trace"][0]["tool_name"] == "nmap_scan"
        assert st["execution_trace"][0]["tool_output"] == "22/tcp open"
        assert st["findings"] == [{"title": "KV"}]  # snapshot overlay wins

    def test_overlay_fills_gaps_only(self, tmp_path):
        from suijin.modules.ops.lib import sje_export

        st = sje_export.derive_graph_state(_events_path(tmp_path), overlay=_frame_state())
        # journal message wins over the unconfirmed live frame
        assert st["messages"] == [
            {"role": "assistant", "content": "recon done"},
            {"role": "user", "content": "try exploit-now"},
        ]
        # live-only keys fill in (the journal never carries them)
        assert st["conversation_objectives"] == ["go deeper"]
        # journal facts never overridden by a stale live frame
        assert st["current_iteration"] == 3

    def test_empty_journal_is_empty(self, tmp_path):
        from suijin.modules.ops.lib import sje_export

        assert sje_export.derive_graph_state(tmp_path / "no" / "events.jsonl") == {}
        bare = tmp_path / "empty.jsonl"
        bare.write_text("")
        assert sje_export.derive_graph_state(bare) == {}

    def test_torn_tail_dropped(self, tmp_path):
        from suijin.modules.ops.lib import sje_export

        p = _events_path(tmp_path, n=2)
        p.write_text(p.read_text() + "\n{crashed-mid-write\n")
        st = sje_export.derive_graph_state(p)
        assert st["current_iteration"] == 3  # torn line silently dropped


class TestDerivedBundle:
    def test_bundle_graph_state_is_the_journal(self, tmp_path):
        p = eb.save_engagement("t", "obj", {"provider": "zai"}, _frame_state(), 0.5, events_path=_events_path(tmp_path))
        gs = eb.load_engagement(p)["graph_state"]
        assert gs["current_iteration"] == 3  # journal, not the frame's 0
        assert gs["messages"][1]["content"] == "try exploit-now"  # journal order
        assert gs["execution_trace"][0]["tool_name"] == "nmap_scan"
        assert gs["conversation_objectives"] == ["go deeper"]  # gap-filled
        assert "completion_reason" not in gs  # resume contract

    def test_falls_back_to_live_state_without_journal(self, tmp_path):
        path = eb.save_engagement("t", "obj", {}, _frame_state(), 0.0)
        gs = eb.load_engagement(path)["graph_state"]
        assert gs["messages"] == [{"role": "assistant", "content": "MID-FLIGHT not confirmed"}]
        assert "completion_reason" not in gs
        # a present-but-empty journal also falls back
        bare = tmp_path / "empty_ev.jsonl"
        bare.write_text("")
        path = eb.save_engagement("t", "obj", {}, _frame_state(), 0.0, events_path=bare)
        gs = eb.load_engagement(path)["graph_state"]
        assert gs["messages"] == [{"role": "assistant", "content": "MID-FLIGHT not confirmed"}]

    def test_derived_bundle_hash_sealed_roundtrip(self, tmp_path):
        path = eb.save_engagement("t", "obj", {}, _frame_state(), 0.0, events_path=_events_path(tmp_path))
        bundle = eb.load_engagement(path)  # verification itself asserts the seal
        from suijin.modules.ops.lib import sje_export

        want = sje_export.derive_graph_state(_events_path(tmp_path), overlay=_frame_state())
        got = bundle["graph_state"]
        # compare over the PROJECTED bundle surface (resume keys only)
        want = {k: want[k] for k in eb.RESUME_KEYS if k in want}
        assert set(got) == set(want)
        for k in want:
            assert got[k] == want[k], f"{k} mismatch"


class TestCrashSaverDerived:
    def test_arm_with_events_path_derives_bundle(self, tmp_path):
        saver = eb.CrashSaver()
        saver.arm(
            "t", "Crash http://t", {"provider": "zai"}, lambda: _frame_state(), events_path=_events_path(tmp_path)
        )
        path = saver.save("crash")
        assert path is not None
        gs = eb.load_engagement(path)["graph_state"]
        assert gs["current_iteration"] == 3  # journal-derived
        assert gs["messages"][1]["content"] == "try exploit-now"

    def test_bind_events_path_after_arm(self, tmp_path):
        saver = eb.CrashSaver()
        saver.arm("t", "Crash http://t", {}, lambda: _frame_state())
        saver.bind_events_path(_events_path(tmp_path))
        path = saver.save("conclusion")
        gs = eb.load_engagement(path)["graph_state"]
        assert gs["current_iteration"] == 3  # bound journal won over the frame

    def test_bind_uncommitted_is_idempotent(self, tmp_path):
        saver = eb.CrashSaver()
        saver.arm("t", "Crash http://t", {}, lambda: _frame_state())
        saver.bind_events_path(_events_path(tmp_path))
        saver.bind_events_path(_events_path(tmp_path))
        saver.save("end")
        assert saver.last_path is not None
