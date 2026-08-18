"""tools — the tool registry core module.

Bridges the existing dispatch surface onto the kernel Context: every
core tool and discovered pack tool becomes ctx-callable during boot.
Phase 3 (pack conversion) moves pack registration here natively; today
the bridge reuses dispatch's route table so behavior is identical.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


def _bridge_tool(fn, tool_name: str):
    """Adapt a dispatch-style route fn(args) -> str to Tool(args, ctx)."""

    def tool(args: dict, ctx) -> str:
        return str(fn(args or {}))

    tool.__name__ = tool_name.replace(".", "_")
    return tool


class ToolsModule(Module):
    id = "tools"
    tier = Tier.CORE

    def __init__(self) -> None:
        self._count = 0

    def register(self, ctx) -> None:
        pass  # bridging happens at start (needs platform's runtime init)

    def start(self, ctx) -> None:
        from suijin.tools import dispatch

        routes = dispatch._build_routes(None)
        bridged = 0
        for name, fn in routes.items():
            # don't clobber anything an earlier module registered
            if ctx.has_tool(name):
                continue
            ctx.register_tool(name, _bridge_tool(fn, name), description="bridged from dispatch", owner="tools")
            bridged += 1
        self._count = bridged
        ctx.journal.append("tools", f"{bridged} tools bridged onto the Context")

    def stop(self, ctx) -> None:
        pass

    @property
    def bridged_count(self) -> int:
        return self._count
