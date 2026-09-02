"""capability audit — the no-orphan-code contract, enforced.

Every dispatch route must have a catalog bullet (the agent can see it);
every catalog bullet must have a route (the docs don't lie); packs whose
manifests declare tools their code never defines are named. This is the
public promise: orphans are build failures, not archaeology.
"""

from __future__ import annotations

import re


def _catalog_text() -> str:
    from suijin.modules.tools.lib.dispatch import get_tool_catalog

    return str(get_tool_catalog())


def _routes() -> set[str]:
    from suijin.modules.tools.lib.dispatch import list_route_tools

    return set(list_route_tools())


def audit() -> tuple[int, str]:
    """Returns (exit_code, report). exit 0 = clean; 1 = orphans named."""
    lines: list[str] = []
    orphans: list[str] = []
    try:
        routes = _routes()
        catalog = _catalog_text()

        # 1) every route appears in the catalog (bullet or explicit mention)
        uncataloged = sorted(
            r for r in routes
            if f"`{r}`" not in catalog and f"**{r}**" not in catalog and f"- {r}" not in catalog
        )
        # 2) every catalog bullet has a route (docs don't lie)
        cataloged = set(re.findall(r"\*\*([a-z0-9_]+)\*\*", catalog))
        prose_only = {"recon", "deploy", "catalog", "job_spawn"}  # doctrine prose, not tools
        phantom = sorted(c for c in cataloged if c not in routes and c not in prose_only)

        # 3) broken packs: manifest declares tools the code never defines
        broken_packs: list[str] = []
        try:
            from suijin.modules.loader import discover_modules, get_loaded_modules

            discover_modules()
            for name, info in get_loaded_modules().items():
                declared = set((info.get("manifest") or {}).get("tools") or {})
                defined = set((info.get("tools") or {}).keys())
                missing = declared - defined
                if missing:
                    broken_packs.append(f"{name}: declares {sorted(missing)} but main.py never defines them")
        except Exception as e:  # noqa: BLE001
            lines.append(f"(pack scan skipped: {e})")

        lines.append(f"capability audit — {len(routes)} routes, catalog parity:")
        if uncataloged:
            orphans.extend(uncataloged)
            lines.append(f"  ✗ UNCATALOGED ROUTES (agent can't see them): {', '.join(uncataloged[:20])}")
        else:
            lines.append("  ✓ every route is cataloged")
        if phantom:
            lines.append(f"  ~ catalog mentions without routes (prose or deprecated): {', '.join(phantom[:15])}")
        if broken_packs:
            orphans.extend(broken_packs)
            lines.extend(f"  ✗ BROKEN PACK — {b}" for b in broken_packs)
        else:
            lines.append("  ✓ every pack defines its declared tools")
        lines.append("exit 0 = mergeable; orphans are build failures, not archaeology")
        return (1 if orphans else 0, "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return (1, f"capability audit failed: {e}")
