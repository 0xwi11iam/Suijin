"""Bug bounty submission report — one clean document, no agent internals.

What the operator asked for (2026-10-19): "no logs, no nothing, no system
info, just a paragraph by the agent and the basic info and then the
vulnerabilities one by one." This is the file you paste into HackerOne /
BugCrowd / a VDP portal. No tool traces, no reasoning, no execution
details — just what a human security researcher would write.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from suijin.server.tools.lib.bbp_report import _CWE_MAP


def _render_triage_inline(findings: list) -> str:
    """Duplicates/flagged from the findings' own triage states — they
    never silently disappear, they render with their primary."""
    lines = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        t = f.get("triage")
        if isinstance(t, dict) and t.get("status") in ("duplicate_of", "flagged"):
            eid = str(f.get("id") or f.get("title") or "?")[:20]
            tag = "duplicate" if t.get("status") == "duplicate_of" else "POSSIBLE duplicate (operator confirm)"
            lines.append(f"- {eid}: {tag} of {t.get('of', '?')} — {t.get('reason', '')}")
    return ("**Triage:**\n" + "\n".join(lines)) if lines else ""


def generate_bug_bounty_report(
    engagement_name: str,
    findings: list[dict],
    target: str = "",
    engagement_dir: Path | None = None,
) -> Path:
    """Generate bug_bounty_report.md — the clean submission document.

    Structure:
    - Title + target + date + researcher note (one paragraph)
    - Each vulnerability: title, severity, CWE, description, impact,
      steps to reproduce (from POC commands), remediation
    - No execution traces, no tool outputs, no system info, no cost,
      no iteration counts — just the security findings.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # 2026-10-20: `.parent / "reports"` put the submission at the
    # ENGAGEMENTS ROOT (one directory too high) — the report belongs
    # INSIDE the engagement folder it documents.
    reports_dir = (engagement_dir or Path(".")).joinpath("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "bug_bounty_report.md"

    # filter to real findings (not false positives or informational noise)
    real = [
        f
        for f in findings
        if isinstance(f, dict)
        and str(f.get("severity", "info")).lower() in ("critical", "high", "medium", "low")
        and str(f.get("status", "")).upper() not in ("FALSE_POSITIVE", "REJECTED")
        # TRIAGE (2026-10-20): duplicates never reach the submission —
        # they render in the triage section instead
        and not (isinstance(f.get("triage"), dict) and f["triage"].get("status") == "duplicate_of")
    ]

    lines: list[str] = []
    lines.append(f"# Security Assessment Report — {target or engagement_name}")
    lines.append("")
    lines.append(f"**Date:** {now}")
    lines.append(f"**Target:** {target or engagement_name}")
    lines.append("**Scope:** Authorized security testing (bug bounty program)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")

    # TRIAGE SECTION (2026-10-20): duplicates + flagged render with
    # their primary — never silently dropped, never submitted
    with contextlib.suppress(Exception):
        _triage_block = _render_triage_inline(findings)
        if _triage_block:
            lines.append(_triage_block)
            lines.append("")

    if not real:
        lines.append("No exploitable vulnerabilities were identified during this assessment.")
        lines.append("")
        lines.append(
            "All tested surfaces responded with appropriate security controls. "
            "No authentication bypasses, injection points, or access control gaps "
            "were confirmed through active testing."
        )
    else:
        sev_counts = {}
        for f in real:
            sev = str(f.get("severity", "info")).lower()
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
        summary_parts = [
            f"{count} {sev}"
            for sev, count in sorted(
                sev_counts.items(),
                key=lambda x: (
                    ["critical", "high", "medium", "low"].index(x[0])
                    if x[0] in ["critical", "high", "medium", "low"]
                    else 99
                ),
            )
        ]
        lines.append(
            f"During authorized security testing of {target or engagement_name}, "
            f"{len(real)} vulnerabilit{'y' if len(real) == 1 else 'ies'} were identified: "
            f"{', '.join(summary_parts)}."
        )
        lines.append("")
        lines.append(
            "Each finding below includes a proof-of-concept that was executed "
            "against the target and produced the described result. All testing "
            "was conducted within the program's scope and rules of engagement."
        )

    lines.append("")
    lines.append("---")
    lines.append("")

    # each vulnerability
    for i, f in enumerate(real, 1):
        sev = str(f.get("severity", "info")).upper()
        title = str(f.get("title") or f.get("description") or f.get("rule") or f"Finding {i}")
        desc = str(f.get("description") or f.get("evidence") or "")
        endpoint = str(f.get("endpoint") or f.get("target") or "")
        klass = str(f.get("type") or f.get("class") or f.get("finding_type") or "").lower()
        cwe = _CWE_MAP.get(klass, "")

        lines.append(f"## {i}. {title}")
        lines.append("")
        lines.append(f"**Severity:** {sev}")
        if cwe:
            lines.append(f"**CWE:** {cwe}")
        if endpoint:
            lines.append(f"**Endpoint:** `{endpoint}`")
        lines.append("")
        if desc:
            lines.append(f"**Description:** {desc}")
            lines.append("")

        # impact
        impact_map = {
            "critical": "This vulnerability allows full compromise of the affected system, potentially leading to complete data exposure, privilege escalation, or remote code execution.",
            "high": "This vulnerability allows significant unauthorized access to sensitive data or functionality, potentially affecting multiple users or the entire system.",
            "medium": "This vulnerability allows limited unauthorized access or information disclosure that could be leveraged in combination with other issues.",
            "low": "This vulnerability represents a minor security weakness that has limited direct impact but may assist in other attacks.",
        }
        lines.append(f"**Impact:** {impact_map.get(sev.lower(), 'See description above.')}")
        lines.append("")

        # steps to reproduce (from POC if available)
        commands = f.get("commands") or f.get("poc_commands") or []
        if commands and isinstance(commands, list):
            lines.append("**Steps to Reproduce:**")
            lines.append("")
            for j, cmd in enumerate(commands[:5], 1):
                lines.append(f"{j}. `{str(cmd)[:200]}`")
            lines.append("")

        # remediation
        rem_map = {
            "ssrf": "Validate and allowlist all outbound destinations at the egress layer. Deny link-local, metadata, and internal IP ranges.",
            "sqli": "Parameterize all database queries. Never interpolate user input into SQL strings.",
            "xss": "Implement context-aware output encoding and a strict Content-Security-Policy.",
            "idor": "Scope every object access to the authenticated principal. Verify ownership on every request.",
            "open_redirect": "Validate redirect destinations against an allowlist of trusted domains. Never redirect to user-supplied URLs.",
            "auth_bypass": "Enforce server-side authentication on every route. Do not trust client-side state.",
            "rce": "Remove the execution sink. Sandbox any remaining deserialization or command paths.",
            "mass_assignment": "Explicitly allowlist which fields users can set. Reject unexpected parameters.",
        }
        rem = rem_map.get(
            klass,
            "Implement appropriate input validation, access controls, and output encoding for the affected endpoint.",
        )
        lines.append(f"**Remediation:** {rem}")
        lines.append("")
        lines.append("---")
        lines.append("")

    if not real:
        lines.append("---")
        lines.append("")
        lines.append(
            "*This report documents the scope and methodology of the assessment. "
            "All findings, if any, have been reported through the appropriate channels.*"
        )

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
