"""agent — the run-loop core module (parent of graph/nodes/memory).

Registration shims for Phase 2: entries point at the existing
implementations (core/agent_graph.py etc.); the module registers the
agent-graph FACTORY as a Context service so any surface (console, fugu,
battle) can build runs without importing the agent package.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class AgentModule(Module):
    id = "agent"
    tier = Tier.CORE

    def register(self, ctx) -> None:
        def _factory(generate_fn, route_tool_fn, max_iterations=100):
            from suijin.core.agent_graph import SuijinAgentGraph

            return SuijinAgentGraph(
                generate_fn=generate_fn,
                route_tool_fn=route_tool_fn,
                max_iterations=max_iterations,
            )

        ctx.register_service("agent_graph", _factory)
        ctx.register_service("agent_state", lambda: __import__("suijin.core.state", fromlist=["AgentState"]).AgentState)

    def start(self, ctx) -> None:
        ctx.journal.append("agent", "run-loop service registered")

    def stop(self, ctx) -> None:
        pass
