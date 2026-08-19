"""Cost governor — hard budget kill switch (D27)."""

from unittest import mock

from suijin.modules.platform.lib.governor import budget_guard, budget_status


def _usage(pct):
    return mock.patch.dict(
        "suijin.modules.providers.lib.USAGE",
        {"est_cost_usd": 4.0, "priced": True, "calls": 1},
    )


class TestGovernor:
    def test_ok_under_budget(self):
        with mock.patch.dict("suijin.modules.providers.lib.USAGE", {"est_cost_usd": 1.0, "priced": True}):
            assert budget_status({"max_cost_usd": 10.0})["action"] == "ok"
            assert budget_guard({"max_cost_usd": 10.0}) is None

    def test_warn_at_threshold(self):
        with mock.patch.dict("suijin.modules.providers.lib.USAGE", {"est_cost_usd": 9.0, "priced": True}):
            st = budget_status({"max_cost_usd": 10.0})
            assert st["action"] == "warn" and st["pct"] == 90.0
            g = budget_guard({"max_cost_usd": 10.0})
            assert g and g.startswith("COST WARN")

    def test_hard_stop_over_budget(self):
        with mock.patch.dict("suijin.modules.providers.lib.USAGE", {"est_cost_usd": 11.0, "priced": True}):
            assert budget_status({"max_cost_usd": 10.0})["action"] == "stop"
            g = budget_guard({"max_cost_usd": 10.0})
            assert g.startswith("COST LIMIT REACHED")

    def test_unpriced_never_stops(self):
        with mock.patch.dict("suijin.modules.providers.lib.USAGE", {"est_cost_usd": 999.0, "priced": False}):
            assert budget_status({"max_cost_usd": 10.0})["action"] == "ok"

    def test_defaults(self):
        with mock.patch.dict("suijin.modules.providers.lib.USAGE", {"est_cost_usd": 0.0, "priced": True}):
            assert budget_status({})["limit"] == 25.0

    def test_graph_stops_on_budget(self):
        """End-to-end: the agent graph must exit with budget_exhausted
        once USAGE crosses the configured limit."""
        import asyncio

        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

        calls = []

        async def fake_generate(messages, config=None, **kw):
            calls.append(1)
            return '{"action": "use_tool", "tool_name": "search_kb", "args": {}, "thought": "t"}'

        with mock.patch.dict("suijin.modules.providers.lib.USAGE", {"est_cost_usd": 99.0, "priced": True}):
            g = SuijinAgentGraph(
                generate_fn=fake_generate,
                route_tool_fn=lambda *a, **k: "ok",
                max_iterations=5,
                run_config={"max_cost_usd": 10.0},
            )
            result = asyncio.run(g.run("budget smoke", thread_id="budget"))
        assert result.get("completion_reason") == "budget_exhausted"
        assert len(calls) <= 1  # loop stopped; the single allowed call is the final summary
