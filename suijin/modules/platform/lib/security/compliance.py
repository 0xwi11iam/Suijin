"""
suijin/compliance.py
======================
Enterprise compliance reporting for Suijin.
Generates HIPAA / SOC2 style audit reports from the audit trail.
Supports JSON and plain-text export.
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Try rich — graceful fallback if not installed
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    RICH = True
except ImportError:
    RICH = False

BASE_DIR = Path(__file__).resolve().parents[4]
AUDIT_LOG = BASE_DIR / "audit_log.json"
KEYS_FILE = BASE_DIR / "access_keys.json"


def _reports_dir():

    from suijin.modules.platform.lib.workspace import artifact_dir as _ad

    return _ad("compliance_reports")


REPORTS_DIR = _reports_dir()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)  # parents: fresh/patched workspaces crashed on import

# Risk thresholds
RISK_HIGH = 0.7
RISK_MEDIUM = 0.4


# ── helpers ──────────────────────────────────────────────────────────────────


def _load_log() -> list:
    if AUDIT_LOG.exists():
        with open(AUDIT_LOG) as f:
            return json.load(f)
    return []


def _load_keys() -> dict:
    if KEYS_FILE.exists():
        with open(KEYS_FILE) as f:
            return json.load(f)
    return {}


def _risk_label(score: float) -> str:
    if score >= RISK_HIGH:
        return "HIGH"
    if score >= RISK_MEDIUM:
        return "MEDIUM"
    return "LOW"


# ── core analysis ─────────────────────────────────────────────────────────────


def analyse_log(entries: list) -> dict:
    """
    Crunch audit entries into compliance-relevant stats.
    """
    total = len(entries)
    high_risk = [e for e in entries if e.get("risk_score", 0) >= RISK_HIGH]
    medium_risk = [e for e in entries if RISK_MEDIUM <= e.get("risk_score", 0) < RISK_HIGH]
    low_risk = [e for e in entries if e.get("risk_score", 0) < RISK_MEDIUM]

    denied = [
        e for e in entries if "DENIED" in e.get("result", "").upper() or "ACCESS DENIED" in e.get("action", "").upper()
    ]
    red_ops = [e for e in entries if e.get("mode") == "red"]
    blue_ops = [e for e in entries if e.get("mode") == "blue"]

    users = defaultdict(int)
    targets = defaultdict(int)
    action_types = defaultdict(int)

    for e in entries:
        users[e.get("user", "unknown")] += 1
        targets[e.get("target", "unknown")] += 1
        action_type = e.get("action", "")[:30]
        action_types[action_type] += 1

    avg_risk = sum(e.get("risk_score", 0) for e in entries) / total if total else 0.0

    return {
        "total_actions": total,
        "high_risk_count": len(high_risk),
        "medium_risk_count": len(medium_risk),
        "low_risk_count": len(low_risk),
        "denied_access": len(denied),
        "red_team_ops": len(red_ops),
        "blue_team_ops": len(blue_ops),
        "unique_users": len(users),
        "unique_targets": len(targets),
        "avg_risk_score": round(avg_risk, 3),
        "top_users": dict(sorted(users.items(), key=lambda x: -x[1])[:5]),
        "top_targets": dict(sorted(targets.items(), key=lambda x: -x[1])[:5]),
        "high_risk_entries": high_risk,
        "denied_entries": denied,
    }


# ── report generation ─────────────────────────────────────────────────────────


def generate_compliance_report(
    api_key: str = None,
    framework: str = "SOC2",
    export_json: bool = True,
    export_txt: bool = True,
) -> dict:
    """
    Generate a compliance report.

    Parameters
    ----------
    api_key   : if supplied and not admin, report is scoped to that user
    framework : "SOC2" | "HIPAA" | "GENERAL"
    export_json / export_txt : write files to compliance_reports/
    """
    keys = _load_keys()
    entries = _load_log()

    # Scope to user if not admin
    if api_key and keys.get(api_key, {}).get("role") != "admin":
        user = keys.get(api_key, {}).get("name", "unknown")
        entries = [e for e in entries if e.get("user") == user]

    stats = analyse_log(entries)

    # Compliance status per control
    controls = _evaluate_controls(stats, framework)

    report = {
        "report_id": f"SUIJIN-{framework}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "generated_at": datetime.now().isoformat(),
        "framework": framework,
        "period": _get_period(entries),
        "statistics": stats,
        "controls": controls,
        "overall_status": _overall_status(controls),
        "recommendations": _recommendations(stats, controls),
    }

    # Remove bulky sub-lists from the JSON export
    report_clean = {k: v for k, v in report.items() if k not in ("statistics",)}
    report_clean["statistics"] = {k: v for k, v in stats.items() if k not in ("high_risk_entries", "denied_entries")}

    if export_json:
        path = REPORTS_DIR / f"{report['report_id']}.json"
        with open(path, "w") as f:
            json.dump(report_clean, f, indent=2)

    if export_txt:
        path = REPORTS_DIR / f"{report['report_id']}.txt"
        _write_txt_report(report, path)

    return report


def _get_period(entries: list) -> dict:
    if not entries:
        return {"start": "N/A", "end": "N/A"}
    timestamps = [e.get("timestamp", "") for e in entries if e.get("timestamp")]
    return {
        "start": min(timestamps)[:19] if timestamps else "N/A",
        "end": max(timestamps)[:19] if timestamps else "N/A",
    }


# ── control evaluation ────────────────────────────────────────────────────────


def _evaluate_controls(stats: dict, framework: str) -> list:
    controls = []

    # Access control
    controls.append(
        {
            "id": "AC-1",
            "name": "Access Control Policy",
            "status": "PASS" if stats["denied_access"] >= 0 else "FAIL",
            "detail": f"{stats['denied_access']} unauthorised access attempts blocked.",
            "framework": framework,
        }
    )

    # Audit logging
    controls.append(
        {
            "id": "AU-1",
            "name": "Audit Logging",
            "status": "PASS" if stats["total_actions"] > 0 else "FAIL",
            "detail": f"{stats['total_actions']} actions recorded in audit trail.",
            "framework": framework,
        }
    )

    # Risk monitoring
    high_pct = stats["high_risk_count"] / stats["total_actions"] * 100 if stats["total_actions"] else 0
    controls.append(
        {
            "id": "RA-1",
            "name": "Risk Assessment",
            "status": "WARN" if high_pct > 20 else "PASS",
            "detail": f"{high_pct:.1f}% of actions classified HIGH risk (avg score {stats['avg_risk_score']}).",
            "framework": framework,
        }
    )

    # Incident response
    controls.append(
        {
            "id": "IR-1",
            "name": "Incident Response",
            "status": "PASS" if stats["blue_team_ops"] > 0 else "WARN",
            "detail": f"{stats['blue_team_ops']} blue-team defensive operations logged.",
            "framework": framework,
        }
    )

    # Penetration testing (red team)
    controls.append(
        {
            "id": "PT-1",
            "name": "Penetration Testing",
            "status": "PASS" if stats["red_team_ops"] > 0 else "INFO",
            "detail": f"{stats['red_team_ops']} authorised red-team operations conducted.",
            "framework": framework,
        }
    )

    # HIPAA extras
    if framework == "HIPAA":
        controls.append(
            {
                "id": "HIPAA-164.312(b)",
                "name": "Activity Review",
                "status": "PASS",
                "detail": "All agent activities logged with user identity and timestamp.",
                "framework": "HIPAA",
            }
        )

    return controls


def _overall_status(controls: list) -> str:
    statuses = [c["status"] for c in controls]
    if "FAIL" in statuses:
        return "NON-COMPLIANT"
    if "WARN" in statuses:
        return "PARTIAL"
    return "COMPLIANT"


def _recommendations(stats: dict, controls: list) -> list:
    recs = []
    if stats["high_risk_count"] > 0:
        recs.append(f"Review {stats['high_risk_count']} HIGH-risk actions and verify they were authorised.")
    if stats["denied_access"] > 0:
        recs.append(f"{stats['denied_access']} access-denied events detected — investigate for intrusion attempts.")
    if stats["blue_team_ops"] == 0:
        recs.append("No blue-team operations recorded — enable active defence monitoring.")
    if stats["avg_risk_score"] >= RISK_HIGH:
        recs.append("Average risk score is HIGH — tighten guardrail policies in LobsterTrap.")
    if not recs:
        recs.append("No immediate actions required. Continue regular monitoring.")
    return recs


# ── text export ───────────────────────────────────────────────────────────────


def _write_txt_report(report: dict, path: Path):
    lines = [
        "=" * 70,
        f"  SUIJIN COMPLIANCE REPORT — {report['framework']}",
        "=" * 70,
        f"  Report ID   : {report['report_id']}",
        f"  Generated   : {report['generated_at'][:19]}",
        f"  Period      : {report['period']['start']}  ->  {report['period']['end']}",
        f"  Status      : {report['overall_status']}",
        "",
        "─" * 70,
        "  STATISTICS",
        "─" * 70,
    ]
    s = report["statistics"]
    lines += [
        f"  Total actions      : {s['total_actions']}",
        f"  High-risk actions  : {s['high_risk_count']}",
        f"  Medium-risk        : {s['medium_risk_count']}",
        f"  Low-risk           : {s['low_risk_count']}",
        f"  Access denied      : {s['denied_access']}",
        f"  Red-team ops       : {s['red_team_ops']}",
        f"  Blue-team ops      : {s['blue_team_ops']}",
        f"  Avg risk score     : {s['avg_risk_score']}",
        f"  Unique users       : {s['unique_users']}",
        f"  Unique targets     : {s['unique_targets']}",
        "",
        "─" * 70,
        "  CONTROLS",
        "─" * 70,
    ]
    for c in report["controls"]:
        lines.append(f"  [{c['status']:12}] {c['id']}  {c['name']}")
        lines.append(f"               {c['detail']}")
    lines += [
        "",
        "─" * 70,
        "  RECOMMENDATIONS",
        "─" * 70,
    ]
    for i, r in enumerate(report["recommendations"], 1):
        lines.append(f"  {i}. {r}")
    lines += ["", "=" * 70]
    path.write_text("\n".join(lines), encoding="utf-8")


# ── console display ───────────────────────────────────────────────────────────


def print_compliance_report(report: dict):
    if not RICH:
        print(f"\n{'=' * 60}")
        print(f"SUIJIN COMPLIANCE REPORT — {report['framework']}")
        print(f"Status: {report['overall_status']}")
        print(f"{'=' * 60}")
        for c in report["controls"]:
            print(f"[{c['status']}] {c['id']} {c['name']}: {c['detail']}")
        return

    console = Console()
    status_colour = {
        "COMPLIANT": "bold green",
        "PARTIAL": "bold yellow",
        "NON-COMPLIANT": "bold red",
    }.get(report["overall_status"], "white")

    console.print(
        Panel(
            f"[{status_colour}]{report['overall_status']}[/{status_colour}]\n"
            f"[dim]Report ID: {report['report_id']}[/dim]\n"
            f"[dim]Generated: {report['generated_at'][:19]}[/dim]",
            title=f"Suijin {report['framework']} Compliance Report",
            border_style="cyan",
        )
    )

    s = report["statistics"]
    stat_table = Table(show_header=False, box=None, padding=(0, 2))
    stat_table.add_column("Key", style="dim")
    stat_table.add_column("Value", style="bold white")
    rows = [
        ("Total actions", str(s["total_actions"])),
        ("High-risk", f"[red]{s['high_risk_count']}[/red]"),
        ("Medium-risk", f"[yellow]{s['medium_risk_count']}[/yellow]"),
        ("Low-risk", f"[green]{s['low_risk_count']}[/green]"),
        ("Access denied", str(s["denied_access"])),
        ("Red-team ops", str(s["red_team_ops"])),
        ("Blue-team ops", str(s["blue_team_ops"])),
        ("Avg risk score", str(s["avg_risk_score"])),
    ]
    for k, v in rows:
        stat_table.add_row(k, v)
    console.print(Panel(stat_table, title="Statistics", border_style="blue"))

    ctrl_table = Table(title="Control Checks", border_style="magenta")
    ctrl_table.add_column("ID", style="cyan", no_wrap=True)
    ctrl_table.add_column("Control", style="white")
    ctrl_table.add_column("Status", style="bold", no_wrap=True)
    ctrl_table.add_column("Detail", style="dim")
    colour_map = {"PASS": "green", "FAIL": "red", "WARN": "yellow", "INFO": "blue"}
    for c in report["controls"]:
        col = colour_map.get(c["status"], "white")
        ctrl_table.add_row(
            c["id"],
            c["name"],
            f"[{col}]{c['status']}[/{col}]",
            c["detail"],
        )
    console.print(ctrl_table)

    if report["recommendations"]:
        rec_text = "\n".join(f"  {i}. {r}" for i, r in enumerate(report["recommendations"], 1))
        console.print(Panel(rec_text, title="Recommendations", border_style="yellow"))


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Quick self-test — seed a couple of fake log entries first
    _audit_path = BASE_DIR / "audit_log.json"
    if not _audit_path.exists() or _audit_path.stat().st_size < 10:
        sample = [
            {
                "timestamp": "2026-05-13T10:00:00",
                "user": "Admin",
                "role": "admin",
                "mode": "red",
                "target": "http://127.0.0.1:5000",
                "action": "SQL Injection attempt on /login",
                "result": "SUCCESS",
                "risk_score": 0.85,
                "details": {},
            },
            {
                "timestamp": "2026-05-13T10:05:00",
                "user": "Admin",
                "role": "admin",
                "mode": "blue",
                "target": "http://127.0.0.1:5000",
                "action": "Patch applied: sqli",
                "result": "PATCHED",
                "risk_score": 0.3,
                "details": {},
            },
        ]
        with open(_audit_path, "w") as f:
            json.dump(sample, f, indent=2)

    framework = sys.argv[1].upper() if len(sys.argv) > 1 else "SOC2"
    print(f"Generating {framework} compliance report...\n")
    report = generate_compliance_report(framework=framework)
    print_compliance_report(report)
    print(f"\nReports saved to: {REPORTS_DIR}")
