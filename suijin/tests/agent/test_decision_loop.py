"""Wave A: the decision loop behaves like a coding agent's.

The contract: minimal four-field calls parse; extras are optional;
nested args survive; two objects -> first; garbage -> ONE specific
retry -> clean parse_failure. The old schema stalled a real
engagement at iteration 1 ('harness demanding a valid action').
"""

import asyncio
import json

from suijin.modules.platform.lib.helpers.parsing import try_parse_llm_decision


class TestTolerantParsing:
    def test_minimal_call_parses(self):
        d, e = try_parse_llm_decision('{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"sqli"},"thought":"t"}')
        assert d and d["tool_name"] == "search_kb", e

    def test_nested_args_survive(self):
        raw = json.dumps({"action": "use_tool", "tool_name": "http_request",
                          "tool_args": {"url": "http://t/x?json={a:1}", "body": {"k": [1, {"n": 2}]}}, "thought": "t"})
        d, _ = try_parse_llm_decision(f"prose ```json\n{raw}\n``` more prose")
        assert d["tool_args"]["body"]["k"][1]["n"] == 2

    def test_two_objects_first_wins(self):
        d, e = try_parse_llm_decision('{"action":"complete","thought":"done"}\n{"action":"use_tool"}')
        assert d and d["action"] == "complete", e

    def test_code_fence_stripped(self):
        d, _ = try_parse_llm_decision('```json\n{"action":"complete","thought":"t"}\n```')
        assert d is not None

    def test_thought_optional_for_complete(self):
        d, e = try_parse_llm_decision('{"action":"complete","completion_reason":"done"}')
        assert d and d["action"] == "complete", e


class TestThinkLoop:
    def _think(self, raw, state=None):
        from suijin.modules.agent.lib.nodes.think_node import think_node

        async def gen(messages, config=None, **kw):
            if isinstance(raw, list):
                return raw.pop(0)
            return raw

        st = {"objective": "o", "target_info": {}, "messages": [], "current_iteration": 1, **(state or {})}
        return asyncio.run(think_node(st, generate_fn=gen, config={}))

    def test_minimal_call_executes(self):
        out = self._think('{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}')
        assert out["_current_step"]["tool_name"] == "search_kb"

    def test_garbage_one_retry_then_clean_failure(self):
        out = self._think(["total garbage not json", "still garbage"])
        assert out.get("completion_reason") == "parse_failure"

    def test_garbage_then_valid_recovers(self):
        out = self._think(["garbage", '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"r"}'])
        assert out["_current_step"]["tool_name"] == "search_kb"

    def test_retry_message_teaches_minimal_format(self):
        from suijin.modules.agent.lib.nodes.think_node import think_node
        seen = []

        async def gen(messages, config=None, **kw):
            seen.append(list(messages))
            return "garbage"

        st = {"objective": "o", "target_info": {}, "messages": [], "current_iteration": 1}
        asyncio.run(think_node(st, generate_fn=gen, config={}))
        retry = seen[1][-1]["content"]
        assert '"action": "use_tool"' in retry and "EXACTLY ONE JSON object" in retry
