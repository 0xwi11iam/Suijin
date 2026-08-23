"""H3 — dispatch-layer anti-repeat + chain failure memory.

Field evidence: one identical failing http_request repeated 80 times
across 9.5 hours. Prompts are suggestions; this is a law.
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clean_guard(monkeypatch):
    from suijin.modules.tools.lib import dispatch as d

    d.reset_repeat_guard()
    yield
    d.reset_repeat_guard()


def _fail_route(monkeypatch, result="HTTP Error: connection refused"):
    from suijin.modules.tools.lib import dispatch as d

    monkeypatch.setattr(d, "_build_routes", lambda c: {"http_request": lambda a: result})
    return d


class TestRepeatGuard:
    def test_blocks_after_three_identical_failures(self, monkeypatch):
        d = _fail_route(monkeypatch)
        args = {"url": "https://dead.invalid/x"}
        assert d.route_tool("http_request", args, {}).startswith("HTTP Error")
        assert d.route_tool("http_request", args, {}).startswith("HTTP Error")
        assert d.route_tool("http_request", args, {}).startswith("HTTP Error")  # 3rd failure recorded
        blocked = d.route_tool("http_request", args, {})
        assert blocked.startswith("BLOCKED")
        assert "3 times" in blocked and "connection refused" in blocked

    def test_alternatives_named(self, monkeypatch):
        d = _fail_route(monkeypatch)
        args = {"url": "https://dead.invalid/x"}
        for _ in range(3):
            d.route_tool("http_request", args, {})
        blocked = d.route_tool("http_request", args, {})
        assert "curl" in blocked.lower() or "http_request" not in blocked  # real alternatives suggested

    def test_different_args_allowed(self, monkeypatch):
        """Payload iteration is legitimate — only IDENTICAL calls block."""
        d = _fail_route(monkeypatch)
        for _ in range(3):
            d.route_tool("http_request", {"url": "https://t.io/a?q=1"}, {})
        assert d.route_tool("http_request", {"url": "https://t.io/a?q=2"}, {}).startswith("HTTP Error")

    def test_success_on_other_key_does_not_clear(self, monkeypatch):
        """A success on a DIFFERENT call must not clear another call's
        failure streak — only the identical call succeeding clears it."""
        from suijin.modules.tools.lib import dispatch as d

        monkeypatch.setattr(d, "_build_routes", lambda c: {"flip": lambda a: "ok" if a.get("go") else "Error: nope"})
        for _ in range(3):
            d.route_tool("flip", {"go": False}, {})
        assert d.route_tool("flip", {"go": True}, {}) == "ok"  # different key, succeeds
        assert d.route_tool("flip", {"go": False}, {}).startswith("BLOCKED")  # streak intact

    def test_kill_switch(self, monkeypatch):
        import os

        old = os.environ.get("SUIJIN_REPEAT_GUARD")
        os.environ["SUIJIN_REPEAT_GUARD"] = "0"
        try:
            d = _fail_route(monkeypatch)
            args = {"url": "https://dead.invalid/x"}
            for _ in range(5):
                assert d.route_tool("http_request", args, {}).startswith("HTTP Error")
        finally:
            if old is None:
                os.environ.pop("SUIJIN_REPEAT_GUARD", None)
            else:
                os.environ["SUIJIN_REPEAT_GUARD"] = old

    def test_informational_results_never_count(self, monkeypatch):
        """'Job X not found.' and similar non-Error strings must not trip
        the guard (job polling returns informational text)."""
        from suijin.modules.tools.lib import dispatch as d

        monkeypatch.setattr(d, "_build_routes", lambda c: {"job_status": lambda a: "Job zzz not found."})
        for _ in range(6):
            assert "not found" in d.route_tool("job_status", {"job_id": "zzz"}, {})


class TestChainFailureMemory:
    def test_failure_populates_memory(self):
        """The 'Recent Failures' prompt section existed but chain_failures_
        memory was never written — dead block. Now every real failure lands."""
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node

        out = asyncio.run(
            execute_tool_node(
                {
                    "_current_step": {
                        "tool_name": "http_request",
                        "tool_args": {"url": "https://nope.invalid/"},
                        "iteration": 3,
                    },
                    "current_phase": "informational",
                },
                route_tool_fn=lambda n, a, c: "HTTP Error: dead",
            )
        )
        last = out["chain_failures_memory"][-1]
        assert last["tool_name"] == "http_request" and last["error_class"]
        assert last["iteration"] == 3

    def test_success_leaves_memory(self):
        from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node

        out = asyncio.run(
            execute_tool_node(
                {
                    "_current_step": {
                        "tool_name": "http_request",
                        "tool_args": {"url": "https://t.io/"},
                        "iteration": 1,
                    },
                    "current_phase": "informational",
                },
                route_tool_fn=lambda n, a, c: "Status: 200",
            )
        )
        assert "chain_failures_memory" not in out

    def test_failures_render_in_chain_context(self):
        from suijin.modules.agent.lib.state import format_chain_context

        ctx = format_chain_context(
            [],
            [{"tool_name": "nmap", "error_class": "transport_error", "error_message": "timeout", "iteration": 4}],
            [],
            [],
        )
        assert "nmap" in ctx and "timeout" in ctx  # the dead section now has data
