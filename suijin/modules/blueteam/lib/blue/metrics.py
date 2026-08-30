"""BF6 — metrics, MTTD/MTTR, session markdown report, ATT&CK heatmap.

MTTD = detected_at - first_event_at (how long the attack ran before we noticed)
MTTR = contained_at - detected_at (how fast we acted after detection)
Dwell = closed_at - first_event_at (total session)

ATT&CK coverage: which techniques detected vs missed.
Session report: auto-generated markdown at engagement end.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from suijin.modules.blueteam.lib.blue.cases import ATTACK_MAP


def _parse_ts(ts: str) -> float | None:
    """Parse an ISO timestamp to epoch seconds (None if unparseable)."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def case_metrics(case: dict) -> dict:
    """MTTD/MTTR/dwell for ONE case (seconds; None when timestamps missing)."""
    first = _parse_ts(case.get("first_event_at", ""))
    detected = _parse_ts(case.get("detected_at", ""))
    contained = _parse_ts(case.get("contained_at", ""))
    closed = _parse_ts(case.get("closed_at", ""))
    return {
        "mttd_s": round(detected - first, 1) if detected and first else None,
        "mttr_s": round(contained - detected, 1) if contained and detected else None,
        "dwell_s": round(closed - first, 1) if closed and first else None,
        "status": case.get("status", "?"),
        "severity": case.get("severity", 0),
    }


def session_metrics(case_store, feed_stats: dict | None = None) -> dict:
    """Full session metrics: detection stats + MTTD/MTTR aggregates +
    ATT&CK coverage."""
    cases = case_store.list_cases()
    all_metrics = [case_metrics(c) for c in cases]

    mttds = [m["mttd_s"] for m in all_metrics if m["mttd_s"] is not None]
    mttrs = [m["mttr_s"] for m in all_metrics if m["mttr_s"] is not None]

    # ATT&CK coverage: which techniques were detected
    detected_techniques = set()
    for c in cases:
        if c.get("mitre"):
            detected_techniques.add(c["mitre"])

    # all known techniques the detector covers
    covered_techniques = {v for v in ATTACK_MAP.values() if v}

    return {
        "cases": len(cases),
        "cases_open": sum(1 for c in cases if c.get("status") != "closed"),
        "cases_closed": sum(1 for c in cases if c.get("status") == "closed"),
        "mttd_avg_s": round(sum(mttds) / len(mttds), 1) if mttds else None,
        "mttr_avg_s": round(sum(mttrs) / len(mttrs), 1) if mttrs else None,
        "mttd_all_s": mttds,
        "mttr_all_s": mttrs,
        "feed": feed_stats or {},
        "attack": {
            "detected": sorted(detected_techniques),
            "covered": sorted(covered_techniques),
            "coverage_pct": round(100 * len(detected_techniques) / max(1, len(covered_techniques)), 0),
        },
        "actors": len({c.get("actor_ip") for c in cases}),
        "by_type": _count_by_type(cases),
    }


def _count_by_type(cases: list[dict]) -> dict:
    counts: dict = {}
    for c in cases:
        t = c.get("attack_type", "?")
        counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def render_session_report(metrics: dict, target: str = "") -> str:
    """The session markdown report — auto-generated at engagement end."""
    lines = [
        f"# Blue Session Report — {target or 'unknown target'}",
        f"*generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        "",
        "## Summary",
        f"- requests processed: {metrics.get('feed', {}).get('total', 0):,}",
        f"- threats detected: {metrics.get('feed', {}).get('detected', 0)}",
        f"- blocked: {metrics.get('feed', {}).get('blocked', 0)}",
        f"- tarpitted: {metrics.get('feed', {}).get('tarpitted', 0)}",
        f"- deceived: {metrics.get('feed', {}).get('deceived', 0)}",
        "",
        "## Cases",
        f"- total: {metrics['cases']} ({metrics['cases_open']} open, {metrics['cases_closed']} closed)",
        f"- actors tracked: {metrics['actors']}",
        "",
    ]

    if metrics.get("by_type"):
        lines.append("### Attack types")
        for atype, count in metrics["by_type"].items():
            lines.append(f"- {atype}: {count}")
        lines.append("")

    # MTTD/MTTR
    lines.append("## Response Times")
    if metrics.get("mttd_avg_s") is not None:
        lines.append(f"- MTTD (avg): {metrics['mttd_avg_s']}s")
    else:
        lines.append("- MTTD: (insufficient data)")
    if metrics.get("mttr_avg_s") is not None:
        lines.append(f"- MTTR (avg): {metrics['mttr_avg_s']}s")
    else:
        lines.append("- MTTR: (insufficient data)")
    lines.append("")

    # ATT&CK coverage
    attack = metrics.get("attack", {})
    lines.append(f"## ATT&CK Coverage ({attack.get('coverage_pct', 0)}%)")
    detected = set(attack.get("detected", []))
    for tech in attack.get("covered", []):
        mark = "✓" if tech in detected else "·"
        lines.append(f"- [{mark}] {tech}")
    lines.append("")

    return "\n".join(lines)


def save_report(metrics: dict, target: str = "") -> Path:
    """Save the session report to outputs/reports/."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "outputs" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = d / f"blue_session_{stamp}.md"
    path.write_text(render_session_report(metrics, target), encoding="utf-8")
    return path
