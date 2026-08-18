"""Tests for the live run command box (RunBox) and the execute_terminal
HITL approvals fix."""

import io
import time

import pytest
from rich.console import Console

from suijin.tools.run_commands import RunBox


def make_box(**kw):
    # string console so output is capturable
    out = Console(file=io.StringIO(), force_terminal=False, width=200)
    box = RunBox(console=out, **kw)
    return box, out


class TestDispatch:
    def test_help_lists_commands(self):
        box, out = make_box()
        box.dispatch("/help")
        text = out.file.getvalue()
        for cmd in ("/state", "/note", "/kb", "/pause", "/panic", "/approvals"):
            assert cmd in text

    def test_state_uses_get_state(self):
        box, out = make_box(get_state=lambda: {"current_phase": "exploit", "current_iteration": 7, "messages": [{}]})
        box.dispatch("/state")
        assert "phase=exploit" in out.file.getvalue()
        assert "iters=7" in out.file.getvalue()

    def test_state_empty_safe(self):
        box, out = make_box(get_state=lambda: {})
        box.dispatch("/state")
        assert "no state yet" in out.file.getvalue()

    def test_unknown_command_hint(self):
        box, out = make_box()
        box.dispatch("/definitely_not_a_command")
        assert "unknown" in out.file.getvalue() and "/help" in out.file.getvalue()

    def test_plain_text_becomes_guidance(self):
        box, out = make_box()
        box.dispatch("focus on the admin panel")
        assert box.take_guidance() == ["focus on the admin panel"]
        # second line stays queued too
        box.dispatch("and check the uploads")
        assert box.take_guidance() == ["and check the uploads"]
        assert box.take_guidance() == []  # drained

    def test_empty_line_noop(self):
        box, _ = make_box()
        box.dispatch("   ")
        assert box.take_guidance() == []

    def test_handler_exception_never_propagates(self):
        box, out = make_box()

        def boom(_args):
            raise RuntimeError("handler exploded")

        box.register("boom", boom)
        box.dispatch("/boom now")  # must not raise
        assert "failed" in out.file.getvalue()

    def test_custom_command_registration(self):
        box, out = make_box()
        box.register("shout", lambda a: out.print(f"LOUD:{a}"))
        box.dispatch("/shout hello world")
        assert "LOUD:hello world" in out.file.getvalue()

    def test_case_insensitive_command(self):
        box, out = make_box()
        box.dispatch("/HELP")
        assert "/state" in out.file.getvalue()


class TestLiveHandlers:
    def test_note_written(self, tmp_path, monkeypatch):
        from suijin.tools import intel

        notes = tmp_path / ".notes"
        monkeypatch.setattr(intel, "NOTES_DIR", notes)
        box, out = make_box()
        box.dispatch("/note found admin panel")
        assert any(notes.glob("*.md"))

    def test_kb_search_live(self):
        box, out = make_box()
        box.dispatch("/kb sqli")
        # with no KB the response is the DISABLED guidance; either way it answers
        text = out.file.getvalue()
        assert ("DISABLED" in text) or ("[" in text)  # answered, not crashed

    def test_kb_requires_query(self):
        box, out = make_box()
        box.dispatch("/kb")
        assert "query required" in out.file.getvalue()

    def test_approve_deny_via_box(self, tmp_path, monkeypatch):
        from suijin.tools import approvals as ap

        monkeypatch.setattr(ap, "APPROVALS_PATH", tmp_path / "a.json")
        monkeypatch.setattr(ap, "SESSION_PATH", tmp_path / "s.json")
        ap.record_pending("hydra_brute", {"target": "10.0.0.9"})
        box, out = make_box()
        box.dispatch("/approvals")
        assert "hydra_brute" in out.file.getvalue()
        box.dispatch("/approve 1")
        assert ap.decision_for("hydra_brute") == "approved"
        box.dispatch("/deny 1")
        assert ap.decision_for("hydra_brute") == "denied"

    def test_approve_requires_numeric(self):
        box, out = make_box()
        box.dispatch("/approve abc")
        assert "numeric id" in out.file.getvalue()

    def test_cost_reports_tally(self):
        from suijin.tools import providers

        providers.reset_usage()
        providers.USAGE.update(
            {"calls": 3, "input_tokens": 100, "output_tokens": 50, "est_cost_usd": 0.0123, "priced": True}
        )
        box, out = make_box()
        box.dispatch("/cost")
        assert "calls=3" in out.file.getvalue() and "$0.0123" in out.file.getvalue()

    def test_pause_sets_interrupt_flag(self):
        import signal as _signal

        box, _ = make_box()
        _signal._suijin_interrupted = False
        box.dispatch("/pause")
        assert _signal._suijin_interrupted is True
        _signal._suijin_interrupted = False

    def test_scope_reports_policy(self, monkeypatch, tmp_path):
        import suijin.tools.governance as gov

        pol = tmp_path / "policy.json"
        pol.write_text('{"allowed_target_scopes": ["10.0.0.0/8"]}')
        monkeypatch.setattr(gov, "POLICY_PATH", pol)
        box, out = make_box()
        box.dispatch("/scope")
        assert "10.0.0.0/8" in out.file.getvalue()


class TestLifecycle:
    def test_start_stop(self):
        box, _ = make_box()
        b = box.start()
        assert b is box
        time.sleep(0.05)
        box.stop()
        time.sleep(0.05)
        assert not box.alive

    def test_stop_idempotent(self):
        box, _ = make_box()
        box.stop()
        box.stop()  # no error

    def test_start_after_stop_is_noop(self):
        box, _ = make_box()
        box.stop()
        box.start()
        assert not box.alive

    def test_reader_survives_closed_stdin(self, monkeypatch):
        # stdin that raises -> thread exits silently, box not alive

        def bad_input():
            raise OSError("closed")

        monkeypatch.setattr("sys.stdin", None)
        box, _ = make_box()
        box.start()
        time.sleep(0.05)
        assert not box.alive  # exited quietly, no exception leaked


class TestHitlTerminalApprovals:
    """The gap: execute_terminal blocked by the binary allowlist never
    queued an approval — the operator couldn't see or approve blocked cmds."""

    @pytest.fixture(autouse=True)
    def _files(self, tmp_path, monkeypatch):
        from suijin.tools import approvals as ap

        monkeypatch.setattr(ap, "APPROVALS_PATH", tmp_path / "a.json")
        monkeypatch.setattr(ap, "SESSION_PATH", tmp_path / "s.json")
        return ap

    def test_blocked_binary_queues_with_reason(self):
        from suijin.tools import dispatch

        dispatch.route_tool("execute_terminal", {"cmd": "hydra -l admin -P wl 10.0.0.1"}, {"mode_hitl": True})
        from suijin.tools.approvals import list_approvals

        items = [i for i in list_approvals() if i["tool"] == "execute_terminal"]
        assert items and items[-1]["args"].get("blocked_binary") == "hydra"

    def test_approving_execute_terminal_allows_binary(self):
        from suijin.tools import approvals as ap
        from suijin.tools import dispatch, modes

        dispatch.route_tool("execute_terminal", {"cmd": "sqlmap -u http://10.0.0.1"}, {"mode_hitl": True})
        item = [i for i in ap.list_approvals() if i["tool"] == "execute_terminal"][-1]
        ap.decide(item["id"], approve=True)
        # verdict honored: HITL check passes for the tool now
        assert (
            modes.check_mode_restrictions("execute_terminal", {"cmd": "sqlmap -u http://10.0.0.1"}, {"mode_hitl": True})
            is None
        )

    def test_denying_reports_in_block_message(self):
        from suijin.tools import approvals as ap
        from suijin.tools import dispatch

        dispatch.route_tool("execute_terminal", {"cmd": "hydra x"}, {"mode_hitl": True})
        item = [i for i in ap.list_approvals() if i["tool"] == "execute_terminal"][-1]
        ap.decide(item["id"], approve=False)
        out = dispatch.route_tool("execute_terminal", {"cmd": "hydra x"}, {"mode_hitl": True})
        assert "DENIED" in out
