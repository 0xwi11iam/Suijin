"""tools — the tool registry core module.

Bridges the existing dispatch surface onto the kernel Context: every
core tool becomes ctx-callable during boot. Pack tools are NOT bridged —
converted packs register their own tools (Phase 3); when no packs are
discovered the bridge falls back to everything, keeping behavior
identical for pack-less boots.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


def _bridge_tool(fn, tool_name: str):
    """Adapt a dispatch-style route fn(args) -> str to Tool(args, ctx)."""

    def tool(args: dict, ctx) -> str:
        return str(fn(args or {}))

    tool.__name__ = tool_name.replace(".", "_")
    return tool


def _core_routes(pack_tree_booted: bool = False) -> dict:
    """Dispatch routes; when the pack tree booted, minus pack-owned tools.

    Pack presence is decided by the BOOT, not ambient discovery — the
    legacy loader discovers core_utils (a pack that re-declares core
    builtins like search_kb) even in pack-less boots, which would wrongly
    strip core tools from the bridge. Only an actually-booted pack tree
    takes ownership.
    """
    from suijin.tools import dispatch

    routes = dict(dispatch._build_routes(None))
    if not pack_tree_booted:
        return routes
    from suijin.modules import _packbridge

    pack_tool_names = _packbridge.all_pack_tool_names()
    return {n: f for n, f in routes.items() if n not in pack_tool_names}


class ToolsModule(Module):
    id = "tools"
    tier = Tier.CORE

    def __init__(self) -> None:
        self._count = 0

    def register(self, ctx) -> None:
        pass  # bridging happens at start (needs platform's runtime init)

    def start(self, ctx) -> None:
        # does the boot include pack modules? (any unit whose id matches a
        # discovered pack directory)
        from suijin.modules import _packbridge

        bridged = 0
        for name, fn in _core_routes(_packbridge.packs_booted(ctx)).items():
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
