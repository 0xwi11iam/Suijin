"""Refusal recovery — authorized work gets the record, not a wall (2026-11-01)."""

from suijin.server.agent.lib import refusal_recovery as rr


class TestDetection:
    def test_vrp_style_refusal_detected(self):
        # the ACTUAL field text shape (the google run's hesitation)
        t = (
            "I'll hold off firing anything else until the program basis is settled. "
            "I'm not able to assist with testing I can't verify is authorized, and a "
            "local authorization file can't substitute for the program's own scope."
        )
        assert rr.is_refusal(t)

    def test_safety_speak_detected(self):
        assert rr.is_refusal("I can't assist with unauthorized access to systems.")
        assert rr.is_refusal("I have ethical concerns about attacking this target.")

    def test_malformed_decision_is_not_refusal(self):
        assert not rr.is_refusal('{"action": "use_tool", "tool_name": "x"')  # broken JSON = work
        assert not rr.is_refusal("curl the endpoint and grep for markers")  # work prose

    def test_empty_and_huge(self):
        assert not rr.is_refusal("")
        assert not rr.is_refusal("x" * 30_000)


class TestRecoveryMessage:
    def test_carries_record_and_directive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path))
        msg = rr.recovery_message({"original_objective": "test.example"})
        assert "AUTHORIZED security testing" in msg
        assert "settled" in msg and "EXACTLY ONE JSON" in msg

    def test_record_embedded_when_on_file(self, tmp_path, monkeypatch):

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path))
        auths = tmp_path / "authorizations"
        auths.mkdir()
        (auths / "t.example.md").write_text("# authorization\nprogram: test\n")
        msg = rr.recovery_message({"original_objective": "t.example"})
        assert "Authorization" in msg or "on file" in msg


class TestEscalationRule:
    def test_counts_and_escalates_at_two(self):
        assert rr.count_state({}) == 0
        p1 = rr.state_patch({})
        assert p1["_consecutive_refusals"] == 1 and "_refusal_escalate" not in p1
        p2 = rr.state_patch({"_consecutive_refusals": 1})
        assert p2["_refusal_escalate"] is True  # second refusal → operator

    def test_reset(self):
        assert rr.reset_patch() == {"_consecutive_refusals": 0}


class TestPromptRidesTheRecord:
    def test_record_injected_after_frame(self, tmp_path, monkeypatch):

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path))
        auths = tmp_path / "authorizations"
        auths.mkdir()
        (auths / "t.example.md").write_text("program: test")
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt({"objective": "t.example", "original_objective": "t.example"})
        assert "AUTHORIZED" in p or "Authorization" in p

    def test_professional_reframe_present(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt({"objective": "x"})
        assert "professional penetration tester" in p
        assert "You are not testing this application. You are ATTACKING it." not in p
