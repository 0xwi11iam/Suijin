"""Golden decision contracts — REAL model shapes from the live trails.

The reliability standard (2026-10-30): the chain must hold, not just each
layer. These fixtures are the exact JSON the model emitted in the field
(events.jsonl seq 67/71 and the standard shapes); every one must survive
normalize → ask extraction → the execute branch's view — ONE opinion
end to end.
"""

from suijin.server.agent.lib import ask
from suijin.server.agent.lib.decision import Action, Decision, normalize_decision

# ── fixtures: verbatim shapes from the 2026-10-29 trail ───────────────

SEQ67_TOPLEVEL_ASK = {
    "action": "ask_operator",
    "question": "Acknowledged — yes, I need an account. Please reply: 1 <email>:<password>",
    "thought": "Operator reminded me they'll provide accounts",
    "auto_actions": [{"action": "write_note", "args": {"content": "..."}}],
}

SEQ71_NESTED_ASK = {
    "action": "ask_operator",
    "tool_args": {"question": "Yes — I need an account on 127.0.0.1:6000. Registration 404s."},
    "thought": "making a single crisp credential request",
}

STANDARD_USE_TOOL = {
    "action": "use_tool",
    "tool_name": "http_request",
    "tool_args": {"method": "GET", "url": "http://127.0.0.1:6000/api/v1/files"},
}

PLAN_TOOLS_SHAPE = {
    "action": "plan_tools",
    "tool_plan": {"steps": [{"tool_name": "http_request", "tool_args": {"url": "http://t/x"}}]},
    "thought": "chain step",
}


class TestNormalizeContract:
    def test_toplevel_ask_question_survives(self):
        n = normalize_decision(SEQ67_TOPLEVEL_ASK)
        assert n["question"].startswith("Acknowledged")
        assert n["action"] == "ask_operator"

    def test_nested_ask_question_lifted(self):
        """THE live bug: seq 71's nested question became 'Need operator
        guidance.' at one site and the real text at another."""
        n = normalize_decision(SEQ71_NESTED_ASK)
        assert n["question"].startswith("Yes — I need an account")

    def test_use_tool_shape_untouched(self):
        n = normalize_decision(STANDARD_USE_TOOL)
        assert n["tool_name"] == "http_request" and n["tool_args"]["url"].endswith("/files")
        assert n["question"] == ""

    def test_plan_tools_passes_through(self):
        n = normalize_decision(PLAN_TOOLS_SHAPE)
        assert "tool_plan" in n and n["action"] == "plan_tools"

    def test_unknown_action_passes_through(self):
        """Unknown actions reach their handlers intact — the first draft
        degraded them to use_tool and silently killed switch_skill."""
        n = normalize_decision({"action": "switch_skill", "skill_switch": {"to_skill": "sqli"}})
        assert n["action"] == "switch_skill"
        assert n["skill_switch"]["to_skill"] == "sqli"  # pass-through keys survive

    def test_missing_action_defaults(self):
        assert normalize_decision({"tool_name": "x"})["action"] == "use_tool"

    def test_all_canonical_keys_always_present(self):
        for fixture in (SEQ67_TOPLEVEL_ASK, SEQ71_NESTED_ASK, STANDARD_USE_TOOL, {}, None):
            n = normalize_decision(fixture)
            for key in ("action", "tool_name", "tool_args", "question", "thought", "reasoning"):
                assert key in n, (fixture, key)

    def test_typed_decision(self):
        d = Decision.from_model(SEQ71_NESTED_ASK)
        assert d.is_ask and d.action is Action.ASK_OPERATOR
        assert d.question.startswith("Yes —")


class TestOneOpinionEndToEnd:
    """The chain contract: think's normalized question IS what the
    execute branch asks IS what the operator answers."""

    def test_both_layers_see_the_same_question(self):
        # think_node normalizes once
        n = normalize_decision(SEQ71_NESTED_ASK)
        think_view = n["question"]
        # the step carries tool_args for the execute branch
        step_args = {"question": think_view}
        # execute re-asks through ask.py — must agree
        execute_view = ask.question_of(step_args)
        assert think_view == execute_view and think_view.startswith("Yes —")

    def test_auth_detection_and_picker(self):
        assert ask.looks_like_auth_question("I need login credentials for the app")
        assert not ask.looks_like_auth_question("which payload class next?")
        picker = ask.auth_picker_question("need login for http://t.example:6000/x")
        assert "AUTH NEEDED: http://t.example:6000" in picker and "[1]" in picker and "[4]" in picker

    def test_choice_parsing_every_shape(self):
        assert ask.parse_auth_choice("1 user:pass").kind == "credentials"
        assert ask.parse_auth_choice("1 user:pass").value == "user:pass"
        assert ask.parse_auth_choice("2 eyJabc").kind == "token"
        assert ask.parse_auth_choice("2").kind == "token"  # value prompted later
        assert ask.parse_auth_choice("3").kind == "browser"
        assert ask.parse_auth_choice("4").kind == "skip"
        assert ask.parse_auth_choice("").kind == "skip"
        assert ask.parse_auth_choice("nonsense").kind == "skip"

    def test_login_url_repair(self):
        assert ask.repair_login_url("127.0.0.1:6000") == "http://127.0.0.1:6000"
        assert ask.repair_login_url("example.com") == "https://example.com"
        assert ask.repair_login_url("http://keep.me/x") == "http://keep.me/x"
        assert ask.repair_login_url("") == ""


class TestFixturesAreReal:
    def test_trail_fixture_files_stay_honest(self):
        """The fixtures ARE the trail shapes — if someone 'fixes' the test
        by editing history, this screams."""
        assert "tool_args" in SEQ71_NESTED_ASK and "question" not in SEQ71_NESTED_ASK
