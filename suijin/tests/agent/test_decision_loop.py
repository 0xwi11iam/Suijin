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
        d, e = try_parse_llm_decision(
            '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"sqli"},"thought":"t"}'
        )
        assert d and d["tool_name"] == "search_kb", e

    def test_nested_args_survive(self):
        raw = json.dumps(
            {
                "action": "use_tool",
                "tool_name": "http_request",
                "tool_args": {"url": "http://t/x?json={a:1}", "body": {"k": [1, {"n": 2}]}},
                "thought": "t",
            }
        )
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

    def test_garbage_retries_then_nonterminal_turn(self):
        # three parse retries -> all garbage -> a NON-TERMINAL turn: the
        # engagement survives a garbage stretch (the no-progress breaker
        # in agent_graph bounds true garbage with a clean stop); never an
        # exception leaking through the mock as llm_error
        out = self._think(["total garbage not json", "still garbage", "more garbage"])
        assert not out.get("completion_reason")  # NOT parse_failure death
        assert "minimal JSON" in out["messages"][-1]["content"]
        assert not out.get("_current_step", {}).get("tool_name")  # falsy — no phantom dispatch

    def test_garbage_then_valid_recovers(self):
        out = self._think(
            ["garbage", '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"r"}']
        )
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


class TestContextWindowWiring:
    """Wave 2: budgets scale with the model's context window (config
    override → models.dev → 1M fallback) and the output cap leaves
    input headroom inside the window."""

    def _think_capturing(self, raw, config):
        from suijin.modules.agent.lib.nodes.think_node import think_node

        seen = []

        async def gen(messages, cfg=None, **kw):
            seen.append(cfg)
            return raw

        st = {"objective": "o", "target_info": {}, "messages": [], "current_iteration": 1}
        out = asyncio.run(think_node(st, generate_fn=gen, config=config))
        return out, seen

    def test_output_cap_shrinks_on_small_window(self):
        _, seen = self._think_capturing(
            '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}',
            {"provider": "p", "context_window": 12_000, "max_tokens_per_request": 8000},
        )
        # est input ≈ 40k chars static ≈ 10k tok; window 12k → headroom forces a shrink
        assert seen and seen[0]["max_tokens_per_request"] < 8000
        assert seen[0]["max_tokens_per_request"] >= 1024  # floored, never zero

    def test_big_window_keeps_configured_cap(self):
        _, seen = self._think_capturing(
            '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}',
            {"provider": "p", "context_window": 1_000_000, "max_tokens_per_request": 8000},
        )
        assert seen and seen[0].get("max_tokens_per_request", 8000) == 8000

    def test_profiler_records_the_real_wire_payload(self):
        from suijin.modules.agent.lib.profiler import record

        st = {"messages": [{"role": "user", "content": "tiny state history"}], "_run_config": {"context_window": 50_000}}
        wire = [
            {"role": "system", "content": "x" * 40_000},
            {"role": "user", "content": "y" * 4_000},
        ]
        prof = record(st, wire)
        assert prof["est_tokens"] == 11_000  # the WIRE (44k chars), not the state history
        assert prof["window_tokens"] == 50_000
        assert prof["window_pct"] == 22.0

    def test_profiler_warns_over_80_percent(self):
        from suijin.modules.agent.lib.profiler import render

        st = {
            "_prompt_profile": {"est_tokens": 90_000, "system_chars": 200_000, "history_chars": 160_000, "messages": 3, "window_tokens": 100_000, "window_pct": 90.0},
            "_prompt_profile_trend": [80_000, 90_000],
        }
        out = render(st)
        assert "90.0%" in out and "WARNING" in out
