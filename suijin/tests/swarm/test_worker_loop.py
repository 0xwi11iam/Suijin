"""Regression tests for the swarm worker loop.

The 2026-11-04 field failure class, locked here so it cannot return:
  1. prose replies (no JSON) treated as {"action": "complete"} —
     intents burned with zero tool calls
  2. `decision` unbound when parsing raised under contextlib.suppress —
     UnboundLocalError killed the agent process
  3. regex JSON extraction collapsing to an INNER object when tool args
     nest two levels deep ({"headers": {"a": {"b": 1}}})
  4. the model never being shown the tool catalog
"""

from suijin.server.swarm import worker_loop
from suijin.server.swarm.worker_loop import (
    _balanced_objects,
    _load_catalog,
    _parse_decision,
)


class TestParseDecision:
    def test_prose_only_is_none_never_complete(self):
        # bug 1: this exact reply once marked an intent 'done' in 4 seconds
        assert _parse_decision("I will start by probing CORS headers.") is None

    def test_prose_then_decision_object(self):
        d = _parse_decision(
            'Let me start. {"action": "use_tool", "tool_name": "http_replay", '
            '"args": {"url": "https://example.com"}}'
        )
        assert d is not None
        assert d["tool_name"] == "http_replay"
        assert d["tool_args"] == {"url": "https://example.com"}

    def test_fenced_json_block_wins(self):
        d = _parse_decision('thinking…\n```json\n{"action": "complete", "reason": "done"}\n```\nmore text')
        assert d["action"] == "complete"

    def test_deeply_nested_args_survive(self):
        # bug 3: the regex picked the inner {"b": 1} and lost the tool name
        d = _parse_decision(
            '{"action": "use_tool", "tool_name": "http_replay", '
            '"args": {"headers": {"a": {"b": 1}}}}'
        )
        assert d["tool_name"] == "http_replay"
        assert d["tool_args"] == {"headers": {"a": {"b": 1}}}

    def test_last_decision_wins_over_earlier_example(self):
        d = _parse_decision(
            'Example: {"action": "complete", "reason": "example"} '
            'Now the real one: {"action": "use_tool", "tool_name": "x", "args": {}}'
        )
        assert d["tool_name"] == "x"

    def test_braces_inside_strings_do_not_nest(self):
        d = _parse_decision('{"action": "use_tool", "tool_name": "t", "args": {"q": "a } b { c"}}')
        assert d["tool_name"] == "t"
        assert d["tool_args"]["q"] == "a } b { c"

    def test_balanced_objects_finds_top_level_only(self):
        text = 'x {"a": {"b": 1}} y {"c": 2} z'
        assert _balanced_objects(text) == ['{"a": {"b": 1}}', '{"c": 2}']

    def test_unterminated_object_is_skipped(self):
        assert _parse_decision('{"action": "use_tool" no close') is None

    def test_json_array_is_not_a_decision(self):
        assert _parse_decision("[1, 2, 3]") is None


class TestCatalog:
    def test_catalog_is_loaded_and_substantial(self):
        # bug 4: the model was never shown the catalog — it replied in prose
        catalog = _load_catalog()
        assert isinstance(catalog, str)
        assert len(catalog) > 1000  # the real catalog is tens of KB

    def test_system_prompt_carries_the_catalog(self):
        msg = worker_loop.SYSTEM_TEMPLATE.format(catalog="CATALOG-BODY")
        assert "CATALOG-BODY" in msg
        assert "use_tool" in msg
        assert "complete" in msg
