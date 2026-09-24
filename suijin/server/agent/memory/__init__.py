"""agent.memory sub-module — state + sessions (shim)."""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class MemorySubmodule(Module):
    id = "agent.memory"
    tier = Tier.CORE

    def register(self, ctx) -> None:
        ctx.register_service(
            "state_schema", lambda: __import__("suijin.modules.agent.lib.state", fromlist=["AgentState"]).AgentState
        )
        ctx.register_service(
            "engagement_store",
            lambda: (
                __import__("suijin.modules.agent.lib.engagement", fromlist=["save_session_state"]).save_session_state
            ),
        )

    def start(self, ctx) -> None:
        pass

    def stop(self, ctx) -> None:
        pass
