"""Engagement metering — efficiency leaderboard + cost forecasting (D28/D30).

Reads the engagement record (audit trail JSONs under outputs/audit_trails)
and answers two operator questions:

  1. What did past engagements cost, and what did they produce?
     (findings/dollar, actions/dollar, per engagement — the leaderboard)
  2. What will the next one cost? (forecast from history: mean/median
     cost per engagement + per action, projected for a stated action count)

Never raises; missing/corrupt trails are skipped and reported.
"""

from __future__ import annotations

import json
import statistics


def _clean_name(raw: str) -> str:
    """Engagement names are often whole disclosure policies pasted as the
    objective — first non-boilerplate line, whitespace-collapsed, 36 chars.
    The old [:40] chop mid-word rendered '# Disclosure Policy - x\n## Co'
    garbage rows."""
    lines = [ln.strip(" #*•-") for ln in str(raw or "?").splitlines()]
    text = next(
        (
            ln
            for ln in lines
            if len(ln) >= 4
            and not ln.lower().startswith(("required", "user agent", "please", "helvetica", "program rules"))
        ),
        lines[0] if lines else "?",
    )
    return " ".join(text.split())[:36] or "?"


def _records() -> list[dict]:
    from suijin.modules.platform.lib.workspace import artifact_dir

    out = []
    for p in sorted(artifact_dir("audit_trails").glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "engagement" not in d:
            continue
        out.append(
            {
                "name": _clean_name(d.get("engagement", "?")),
                "cost": float(d.get("cost_usd") or 0.0),
                "actions": int(d.get("total_actions") or 0),
                "ok_actions": int(d.get("successful_actions") or 0),
                "findings": len(d.get("findings") or []),
                "ended": str(d.get("ended", "?"))[:19],
            }
        )
    return out


def leaderboard_table(limit: int = 15):
    """Rich Table version of the leaderboard — the operator-facing render."""
    from rich.table import Table

    GREEN = "#3fb950"

    t = Table(title=None, box=None, pad_edge=False, show_header=True, header_style="dim")
    t.add_column("engagement", style="white", max_width=32, no_wrap=True, overflow="ellipsis")
    t.add_column("find", justify="right", style="bold cyan", width=4, no_wrap=True)
    t.add_column("actions", justify="right", style="dim", width=7, no_wrap=True)
    t.add_column("cost", justify="right", width=7, no_wrap=True)
    t.add_column("efficiency", justify="right", no_wrap=True)
    for r in sorted(
        [r for r in _records() if r["actions"] > 0],
        key=lambda x: -(x["findings"] / x["cost"] if x["cost"] > 0 else 0.0),
    )[:limit]:
        if r["cost"] > 0:
            eff = f"{r['findings'] / r['cost']:.1f} find/$ · ${r['cost'] / max(r['actions'], 1):.3f}/act"
            cost = f"${r['cost']:.2f}"
        else:
            eff = f"{r['findings']} find · free run"
            cost = "[dim]$0.00[/dim]"
        find_style = f"{GREEN}" if r["findings"] else "dim"
        t.add_row(r["name"], f"[{find_style}]{r['findings']}[/{find_style}]", str(r["actions"]), cost, eff)
    return t


def leaderboard(limit: int = 15) -> str:
    """Findings-per-dollar (and actions-per-dollar) per engagement."""
    recs = [r for r in _records() if r["actions"] > 0]
    if not recs:
        return "No completed engagements yet — the leaderboard fills as trails land."
    rows = []
    for r in sorted(recs, key=lambda x: -(x["findings"] / x["cost"] if x["cost"] > 0 else 0.0)):
        if r["cost"] > 0:
            eff = f"{r['findings'] / r['cost']:.1f} find/$"
            ape = f"${r['cost'] / max(r['actions'], 1):.3f}/action"
        else:
            eff = f"{r['findings']} find · free run"
            ape = "$0.000/action"
        rows.append(
            f"  {r['name']:40} {r['findings']:>3} findings {r['actions']:>4} actions ${r['cost']:>7.3f}  {eff:>14}  {ape}"
        )
    total_cost = sum(r["cost"] for r in recs)
    total_find = sum(r["findings"] for r in recs)
    head = f"{len(recs)} engagements | ${total_cost:.2f} total | {total_find} findings"
    if total_cost > 0:
        head += f" | {total_find / total_cost:.2f} findings/$ overall"
    return head + "\n" + "\n".join(rows[:limit])


def forecast(action_count: int | None = None) -> str:
    """Projected cost for the next engagement from history."""
    recs = [r for r in _records() if r["cost"] > 0]
    if len(recs) < 2:
        return "Not enough priced history to forecast (need 2+ engagements with cost data)."
    costs = [r["cost"] for r in recs]
    per_action = [r["cost"] / max(r["actions"], 1) for r in recs if r["actions"] > 0]
    mean = statistics.mean(costs)
    median = statistics.median(costs)
    lo, hi = min(costs), max(costs)
    lines = [
        f"history: {len(recs)} priced engagements | mean ${mean:.2f} | median ${median:.2f} | range ${lo:.2f}-${hi:.2f}",
        f"per-action: mean ${statistics.mean(per_action):.4f}" if per_action else "",
    ]
    if action_count and action_count > 0:
        if per_action:
            proj = statistics.mean(per_action) * action_count
            lines.append(f"forecast for ~{action_count} actions: ${proj:.2f} (per-action model)")
        lines.append(f"forecast for ~{action_count} actions: ~${median:.2f} (median engagement)")
    lines.append("set max_cost_usd in config.json — the governor hard-stops past it")
    return "\n".join(x for x in lines if x)
