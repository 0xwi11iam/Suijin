"""Completion scoreboard — one number the agent and operator both see.

The CyberStrike methodology insight: their agent can call
methodology_status to check whether it's done (coverage %, VRT %,
blocking violations). Suijin's agent has to GUESS — it tries to
complete and gets refused, with no way to check beforehand.

This module computes the scoreboard from the same state the gates read:
coverage cells, chain obligations, depth-gate footholds, budget. Both
the agent (via the completion_status tool) and the operator console
read the same number.
"""

from __future__ import annotations


def scoreboard(state: dict) -> dict:
    """Compute the completion scoreboard. Returns a dict with:
    - can_complete: bool
    - blockers: list[str] (human-readable, one per gate)
    - coverage_pct: float (tested cells / total cells)
    - chain_pending: bool
    - budget_remaining: int (−1 = unlimited)
    """
    blockers: list[str] = []

    # ── coverage gate ─────────────────────────────────────────────────
    from suijin.modules.tools.lib.coverage import untested

    assets = list({str(s.get("surface", "")) for s in (state.get("_attack_queue") or [])[:20]}) or [
        str(state.get("_objective") or "")[:100]
    ]
    ute = untested(assets, limit=999)
    total_cells = len(ute) + _tested_count(state)
    coverage_pct = round(100 * _tested_count(state) / max(1, total_cells), 1)
    if len(ute) > 3:
        top = ", ".join(f"{u['vuln_class']}@{u['asset'].split('//')[-1][:25]}" for u in ute[:3])
        blockers.append(f"Coverage: {len(ute)} high-priority cells untested (top: {top})")

    # ── chain gate ────────────────────────────────────────────────────
    chain = state.get("_chain_pending") or {}
    if chain and not chain.get("explored"):
        blockers.append(f"Chain: CONFIRMED finding '{str(chain.get('finding', '?'))[:60]}' sits unexploited")

    # ── depth gate (footholds with untested unlocks) ──────────────────
    footholds = state.get("_footholds") or []
    untested_unlocks = [f for f in footholds if f.get("status") == "open" and not f.get("unlock_targets")]
    if untested_unlocks:
        blockers.append(f"Depth: {len(untested_unlocks)} confirmed capabilities have untested unlocks")

    # ── budget ────────────────────────────────────────────────────────
    max_iter = int(state.get("max_iterations") or 0)
    current = int(state.get("current_iteration") or 0)
    budget_remaining = max_iter - current if max_iter > 0 else -1

    # ── impact (2026-10-20, go-bigger) ────────────────────────────────
    impact = _impact_score(state)

    return {
        "can_complete": len(blockers) == 0,
        "blockers": blockers,
        "coverage_pct": coverage_pct,
        "chain_pending": bool(chain and not chain.get("explored")),
        "budget_remaining": budget_remaining,
        "impact_score": impact["total"],
        "saturation": impact["satiation"],
    }


def render_scoreboard(state: dict) -> str:
    """completion_status tool: the agent checks whether it can complete."""
    sb = scoreboard(state)
    lines = [
        f"COMPLETION STATUS: {'✅ CAN COMPLETE' if sb['can_complete'] else '❌ BLOCKED'}",
        f"  Coverage: {sb['coverage_pct']}% tested",
        f"  Impact: {sb.get('impact_score', 0)} (tested-cell weights + confirmed findings)",
        f"  Chain pending: {'YES' if sb['chain_pending'] else 'no'}",
        f"  Budget: {sb['budget_remaining']} iterations remaining"
        if sb["budget_remaining"] >= 0
        else "  Budget: unlimited",
    ]
    sat = sb.get("saturation") or {}
    if sat.get("saturated"):
        lines.append(
            f"  ⚠ SURFACE SATURATED: {sat.get('trivia', 0)} trivia findings, {sat.get('real', 0)} real — "
            "stop scanning, start weaponizing (auth_forge / cred_matrix / race_test)"
        )
    if sb["blockers"]:
        lines.append("  Blockers:")
        for b in sb["blockers"]:
            lines.append(f"    — {b}")
    lines.append("")
    if sb["can_complete"]:
        lines.append("All gates green — you may complete.")
    else:
        lines.append("Resolve the blockers above, then complete.")
    return "\n".join(lines)


def _impact_score(state: dict) -> dict:
    """Impact = tested-cell weights + confirmed-finding weights. The
    number the game rewards now: testing authn@target (12) moves it,
    testing info@target (1) barely does."""
    import contextlib

    total = 0
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib.weaponization import class_weight, finding_weight, satiation
        from suijin.modules.tools.lib.coverage import _load

        d = _load()
        for key, c in (d.get("cells") or {}).items():
            if c.get("status") in ("tested_vulnerable", "tested_not_vulnerable", "not_applicable"):
                vc = str(key).rsplit("|", 1)[-1]
                total += class_weight(vc)
        total += sum(finding_weight(f) for f in (state.get("findings") or []) if isinstance(f, dict))
        return {"total": total, "satiation": satiation(state)}
    return {"total": total, "satiation": {"saturated": False, "trivia": 0, "real": 0}}


def _tested_count(state: dict) -> int:
    """Count tested coverage cells from the coverage ledger."""
    with __import__("contextlib").suppress(Exception):
        from suijin.modules.tools.lib.coverage import _load

        d = _load()
        cells = d.get("cells") or {}
        return sum(
            1
            for c in cells.values()
            if c.get("status") in ("tested_vulnerable", "tested_not_vulnerable", "not_applicable")
        )
    return 0
