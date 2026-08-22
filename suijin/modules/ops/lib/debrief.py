"""Engagement debrief — analytics over suijin_agent/audit_trails/*.json.

`suijin debrief` answers: what did we run, what worked, what did it cost,
and what keeps failing — across one engagement or the whole history.
Pure offline reads, no API keys.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def _ws():
    """Platform workspace accessors, resolved lazily (module boundary rule)."""
    from suijin.modules.platform.lib import workspace

    return workspace


def load_audits(audit_dir: Path | None = None) -> list[dict]:
    """Load every audit trail, oldest first. Returns [] when none exist."""
    from suijin.modules.platform.lib.workspace import artifact_dir as _ad

    d = Path(audit_dir) if audit_dir else _ad("audit_trails")
    out: list[dict] = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            t = json.loads(f.read_text())
            t["_file"] = f.name
            out.append(t)
        except (OSError, ValueError):
            continue
    return out


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def engagement_stats(trail: dict) -> dict:
    """Per-engagement metrics from one audit trail."""
    iters = trail.get("iterations", [])
    start = _parse_iso(trail.get("started"))
    end = _parse_iso(trail.get("ended"))
    duration_s = (end - start).total_seconds() if start and end else None

    tools = Counter()
    per_tool_success: dict[str, list[bool]] = {}
    phases = Counter()
    for it in iters:
        act = it.get("action", {})
        tool = act.get("tool") or "none"
        ok = bool(act.get("success"))
        tools[tool] += 1
        per_tool_success.setdefault(tool, []).append(ok)
        if it.get("phase"):
            phases[it["phase"]] += 1

    tool_success = {t: f"{sum(v)}/{len(v)}" for t, v in per_tool_success.items()}
    sev = Counter(f.get("severity", "?").upper() for f in trail.get("findings", []))

    return {
        "engagement": trail.get("engagement", trail.get("_file", "?")),
        "file": trail.get("_file", ""),
        "started": trail.get("started", ""),
        "duration_s": duration_s,
        "actions": trail.get("total_actions", len(iters)),
        "success": trail.get("successful_actions"),
        "failed": trail.get("failed_actions"),
        "findings": len(trail.get("findings", [])),
        "findings_by_severity": dict(sev),
        "cost_usd": trail.get("cost_usd", 0.0),
        "tools": dict(tools.most_common()),
        "tool_success": tool_success,
        "phases": dict(phases.most_common()),
    }


def fleet_stats(trails: list[dict]) -> dict:
    """Cross-engagement trends."""
    if not trails:
        return {"engagements": 0}
    stats = [engagement_stats(t) for t in trails]
    durations = [s["duration_s"] for s in stats if s["duration_s"]]
    all_tools: Counter = Counter()
    for s in stats:
        all_tools.update(s["tools"])
    findings = sum(s["findings"] for s in stats)
    cost = sum(s["cost_usd"] for s in stats)
    return {
        "engagements": len(stats),
        "total_actions": sum(s["actions"] for s in stats),
        "total_findings": findings,
        "total_cost_usd": cost,
        "avg_findings_per_engagement": findings / len(stats),
        "avg_duration_s": (sum(durations) / len(durations)) if durations else None,
        "top_tools": dict(all_tools.most_common(8)),
        "first_engagement": stats[0]["started"],
        "latest_engagement": stats[-1]["started"],
    }


def render_debrief(trails: list[dict], verbose: bool = False) -> str:
    """Human-readable debrief. One line per engagement + fleet trends."""
    if not trails:
        return (
            "No audit trails found — run an engagement first "
            "(suijin -> Red Team). Artifacts land in suijin_agent/audit_trails/."
        )
    lines: list[str] = []
    stats = [engagement_stats(t) for t in trails]

    lines.append(f"ENGAGEMENTS ({len(stats)}):")
    lines.append(
        f"  {'engagement':24} {'actions':>8} {'ok':>6} {'fail':>6} {'findings':>9} {'cost':>9} {'duration':>10}"
    )
    for s in stats:
        dur = f"{s['duration_s'] / 60:.0f}m" if s["duration_s"] else "?"
        lines.append(
            f"  {s['engagement'][:24]:24} {s['actions']:>8} {str(s['success']):>6} "
            f"{str(s['failed']):>6} {s['findings']:>9} "
            f"${s['cost_usd']:>8.4f} {dur:>10}"
        )

    fleet = fleet_stats(trails)
    lines.append("")
    lines.append("FLEET TRENDS:")
    lines.append(
        f"  total: {fleet['total_actions']} actions, "
        f"{fleet['total_findings']} findings, ${fleet['total_cost_usd']:.4f} spent"
    )
    if fleet.get("avg_duration_s"):
        lines.append(f"  avg engagement duration: {fleet['avg_duration_s'] / 60:.1f} min")
    lines.append(f"  avg findings/engagement: {fleet['avg_findings_per_engagement']:.1f}")
    if fleet.get("top_tools"):
        top = ", ".join(f"{t} ({n})" for t, n in list(fleet["top_tools"].items())[:8])
        lines.append(f"  top tools: {top}")

    if verbose:
        lines.append("")
        lines.append("PER-ENGAGEMENT DETAIL:")
        for s in stats:
            lines.append(f"  == {s['engagement']} ({s['started'][:19]})")
            if s["findings_by_severity"]:
                sev = ", ".join(f"{k}:{v}" for k, v in sorted(s["findings_by_severity"].items()))
                lines.append(f"     findings by severity: {sev}")
            if s["tools"]:
                lines.append(f"     tools: {', '.join(f'{t}x{n}' for t, n in list(s['tools'].items())[:10])}")
            fails = {
                t: r
                for t, r in s["tool_success"].items()
                if not r.split("/")[0].isdigit() or int(r.split("/")[0]) < int(r.split("/")[1])
            }
            if fails:
                lines.append(f"     tools failing: {', '.join(f'{t} ({r})' for t, r in fails.items())}")
    return "\n".join(lines)
