"""agent.nodes sub-module — graph nodes (shim)."""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


class NodesSubmodule(Module):
    id = "agent.nodes"
    tier = Tier.CORE

    def register(self, ctx) -> None:
        def _nodes():
            import suijin.modules.agent.lib.nodes as pkg

            return {
                n: getattr(pkg, n)
                for n in ("think_node", "execute_tool_node", "initialize_node", "generate_response_node")
                if hasattr(pkg, n)
            }

        ctx.register_service("nodes", _nodes)

    def start(self, ctx) -> None:
        pass

    def stop(self, ctx) -> None:
        pass
