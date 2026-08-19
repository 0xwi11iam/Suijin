"""The kernel core — resolve_dag and check_paths (pure stdlib).

v4.1: the compiled Rust accelerator (native/suijin-core) was RETIRED.
The boot DAG is 61 units and resolves in milliseconds with this
implementation; the crate's build complexity bought nothing the pure
path didn't already deliver byte-identically (the oracle suite that
proved that equality now pins THIS implementation directly — fixtures,
locked semantics, and 300-tree fuzz properties, all preserved).

Deliberately standalone: no imports from the rest of the kernel.
"""

from __future__ import annotations

import json
from typing import Any

TIER_VALUE = {"core": 0, "recommended": 1, "installed": 2}


# ─── resolve_dag ──────────────────────────────────────────────────────


def resolve_dag(manifests_json: str) -> str:
    manifests = json.loads(manifests_json)
    report = _resolve(manifests)
    return json.dumps(report, sort_keys=True)


def _resolve(manifests: list[dict[str, Any]]) -> dict:
    bootable: set[str] = set()
    skipped: dict[str, str] = {}
    quarantined: dict[str, str] = {}
    collisions: list[list[str]] = []
    overridden: list[str] = []

    # 1. quarantine broken manifests
    by_id: dict[str, list[dict]] = {}
    for m in manifests:
        if m.get("broken"):
            quarantined[m["id"]] = m["broken"]
        else:
            by_id.setdefault(m["id"], []).append(m)

    # 2. collision policy: lowest tier wins unless a higher tier declares
    #    the id in overrides
    winners: dict[str, dict] = {}
    for mid in sorted(by_id):
        candidates = sorted(by_id[mid], key=lambda m: TIER_VALUE.get(m.get("tier", "recommended"), 2))
        winner = candidates[0]
        for cand in candidates[1:]:
            if mid in (cand.get("overrides") or []):
                winner = cand
                overridden.append(mid)
            else:
                # record as [id, loser_tier] — lists, to match serde tuples
                collisions.append([mid, cand.get("tier", "recommended")])
        winners[mid] = winner

    # 3. cycle detection (iterative DFS with coloring; cycle named)
    color = {mid: 0 for mid in winners}
    in_cycle: set[str] = set()
    cycle_desc: dict[str, str] = {}
    for start in sorted(winners):
        if color[start] != 0:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = [start]
        found_cycle: list[str] = []
        while stack:
            node, idx = stack.pop()
            if idx == 0:
                color[node] = 1
            deps = winners[node].get("requires") or []
            if idx < len(deps):
                stack.append((node, idx + 1))
                dep = deps[idx]
                if dep not in winners:
                    continue  # missing handled later
                if color[dep] == 1:
                    pos = path.index(dep) if dep in path else 0
                    found_cycle = list(path[pos:]) + [dep]
                    break
                elif color[dep] == 0:
                    path.append(dep)
                    stack.append((dep, 0))
            else:
                color[node] = 2
                if path and path[-1] == node:
                    path.pop()
        if found_cycle:
            desc = " -> ".join(found_cycle)
            for member in found_cycle:
                in_cycle.add(member)
                cycle_desc[member] = f"dependency cycle: {desc}"

    # 4. availability fixpoint
    pending: dict[str, dict] = dict(winners)
    for mid, desc in cycle_desc.items():
        skipped[mid] = desc
        pending.pop(mid, None)

    changed = True
    while changed:
        changed = False
        for pid in sorted(pending):
            unit = pending[pid]
            missing = [d for d in (unit.get("requires") or []) if d not in winners]
            if missing:
                skipped[pid] = "missing dependency: " + ", ".join(missing)
                del pending[pid]
                changed = True
                continue
            unready = [d for d in (unit.get("requires") or []) if d not in bootable]
            if unready:
                continue
            bootable.add(pid)
            del pending[pid]
            changed = True
    for pid, unit in pending.items():
        blocked = [d for d in (unit.get("requires") or []) if d not in bootable]
        skipped[pid] = "dependencies unavailable: " + ", ".join(blocked)

    # 5. core-missing aborts
    core_problems = sorted(
        mid for mid, u in winners.items() if TIER_VALUE.get(u.get("tier"), 2) == 0 and mid in skipped
    )
    if core_problems:
        details = "; ".join(f"{mid} ({skipped[mid]})" for mid in core_problems)
        return {
            "boot_order": [],
            "bootable": [],
            "skipped": skipped,
            "quarantined": quarantined,
            "collisions": collisions,
            "overridden": overridden,
            "aborted": True,
            "abort_reason": f"core module(s) unavailable: {details}",
        }

    # 6. topological order (alphabetical among ready)
    order: list[str] = []
    placed: set[str] = set()
    while len(placed) < len(bootable):
        ready = sorted(
            mid
            for mid in bootable - placed
            if all(d in placed or d not in bootable for d in (winners[mid].get("requires") or []))
        )
        if not ready:
            break
        for r in ready:
            order.append(r)
            placed.add(r)

    return {
        "boot_order": order,
        "bootable": sorted(bootable),
        "skipped": skipped,
        "quarantined": quarantined,
        "collisions": collisions,
        "overridden": overridden,
        "aborted": False,
        "abort_reason": "",
    }


# ─── check_paths ──────────────────────────────────────────────────────


def _normalize(p: str) -> str:
    """Lexical normalization — resolve . and .. without touching disk."""
    absolute = p.startswith("/")
    out: list[str] = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not absolute:
                out.append("..")
        else:
            out.append(part)
    joined = "/".join(out)
    return f"/{joined}" if absolute else joined


def _is_within(child: str, base: str) -> bool:
    if child == base:
        return True
    child = child.rstrip("/")
    base = base.rstrip("/")
    return child.startswith(base + "/")


def check_paths(paths_json: str) -> str:
    data = json.loads(paths_json)
    root = _normalize(data["root"])
    allow = [_normalize(a) for a in (data.get("allow") or [])]
    out: dict[str, bool] = {}
    for raw in data["paths"]:
        joined = _normalize(raw) if raw.startswith("/") else _normalize(f"{root}/{raw}")
        out[raw] = _is_within(joined, root) or any(_is_within(joined, a) for a in allow)
    return json.dumps(out, sort_keys=True)


def source() -> str:
    """Which implementation answered — always the pure core since v4.1."""
    return "pure-python"
