"""tools — the tool registry core module.

Bridges the existing dispatch surface onto the kernel Context: every
core tool becomes ctx-callable during boot. Pack tools are NOT bridged —
converted packs are self-contained and register their own tools; when
no pack modules actually booted, the bridge falls back to everything,
keeping behavior identical for pack-less boots.
"""

from __future__ import annotations

from suijin.kernel.contracts import Module, Tier


def _bridge_tool(fn, tool_name: str):
    """Adapt a dispatch-style route fn(args) -> str to Tool(args, ctx)."""

    def tool(args: dict, ctx) -> str:
        return str(fn(args or {}))

    tool.__name__ = tool_name.replace(".", "_")
    return tool


def _booted_pack_ids(ctx) -> set[str]:
    """Ids of pack modules that actually booted in this Context."""
    booted = getattr(ctx, "_booted_unit_ids", None) or set()
    return {
        i
        for i in booted
        if "." not in i
        and i not in {"platform", "tools", "agent", "console", "providers", "redteam", "blueteam", "knowledge", "ops"}
    }


def _pack_owned_tools(packs_root) -> set[str]:
    """Tool names declared in booted packs' manifests (files, not the
    legacy loader registry — which mixes core builtins in)."""
    import json

    names: set[str] = set()
    if packs_root and packs_root.is_dir():
        for mf in packs_root.rglob("manifest.json"):
            try:
                names.update((json.loads(mf.read_text()).get("tools") or {}).keys())
            except (OSError, ValueError):
                continue
    return names


def _core_routes(pack_manifest_roots=None) -> dict:
    """Dispatch routes minus pack-owned tools (packs self-register).

    When a pack tree is present, every tool DECLARED in any booted pack's
    manifest is excluded — even if the bridge runs before the pack's
    start() (core tier boots first). The pack owns its declarations;
    the bridge must never squat on them."""
    from suijin.tools import dispatch

    routes = dict(dispatch._build_routes(None))
    if not pack_manifest_roots:
        return routes
    pack_tool_names: set[str] = set()
    for root in pack_manifest_roots:
        pack_tool_names |= _pack_owned_tools(root)
    return {n: f for n, f in routes.items() if n not in pack_tool_names}


class ToolsModule(Module):
    id = "tools"
    tier = Tier.CORE

    def __init__(self) -> None:
        self._count = 0

    def register(self, ctx) -> None:
        pass  # bridging happens at start (needs platform's runtime init)

    def start(self, ctx) -> None:
        # any booted unit with a manifest.json beside its plugin.json = a
        # converted pack tree; its declared tools are pack-owned
        sources = getattr(ctx, "_unit_sources", {}) or {}
        pack_roots = [Path(src) for src in sources.values() if src and (Path(src) / "manifest.json").exists()]
        bridged = 0
        for name, fn in _core_routes(pack_roots).items():
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


from pathlib import Path  # noqa: E402 — module-level constant for typing above
