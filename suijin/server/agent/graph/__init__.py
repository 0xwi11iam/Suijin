"""agent.graph sub-module — LangGraph machine construction (shim)."""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class GraphSubmodule(Module):
    id = "agent.graph"
    tier = Tier.CORE

    def register(self, ctx) -> None:
        ctx.register_service(
            "graph_builder",
            lambda: __import__("suijin.modules.agent.lib.agent_graph", fromlist=["SuijinAgentGraph"]).SuijinAgentGraph,
        )

    def start(self, ctx) -> None:
        pass

    def stop(self, ctx) -> None:
        pass
