"""Ask-operator question extraction — the live-proved ask-loop root cause.

Trail evidence (2026-10-29 engagement, events seq 67→71): the model nested
its question in tool_args; the top-level-only read fell back to
'Need operator guidance.' — the operator answered a GENERIC panel, their
answer never connected, and the model asked again 85s later. Loop.
"""

from suijin.server.agent.lib.nodes.think_node import _extract_ask_question


class TestTolerantExtraction:
    def test_top_level_question(self):
        assert _extract_ask_question({"question": "creds please?"}) == "creds please?"

    def test_tool_args_nested(self):
        # the EXACT live shape from the trail (seq 71)
        d = {"action": "ask_operator", "tool_args": {"question": "Yes — I need an account on 127.0.0.1:6000"}}
        assert _extract_ask_question(d) == "Yes — I need an account on 127.0.0.1:6000"

    def test_args_nested(self):
        d = {"action": "ask_operator", "args": {"question": "token please"}}
        assert _extract_ask_question(d) == "token please"

    def test_q_shorthand(self):
        d = {"tool_args": {"q": "short form"}}
        assert _extract_ask_question(d) == "short form"

    def test_top_level_wins_over_nested(self):
        d = {"question": "top", "tool_args": {"question": "nested"}}
        assert _extract_ask_question(d) == "top"

    def test_blank_top_level_falls_to_nested(self):
        d = {"question": "   ", "tool_args": {"question": "real one"}}
        assert _extract_ask_question(d) == "real one"

    def test_fallback_only_when_truly_empty(self):
        assert _extract_ask_question({}) == "Need operator guidance."
        assert _extract_ask_question(None) == "Need operator guidance."
        assert _extract_ask_question({"tool_args": "a string not dict"}) == "Need operator guidance."


class TestExecutePath:
    def test_execute_branch_reads_nested_question(self, tmp_path, monkeypatch):
        """The execute-side branch (auth redirect lives there) must see the
        REAL question — the keyword scan ('credentials', 'login') only
        matches when the text arrived."""
        import inspect

        from suijin.server.agent.lib.nodes import execute_tool_node as etn

        src = inspect.getsource(etn)
        assert 'tool_args.get("q")' in src and 'tool_args.get("args")' in src
