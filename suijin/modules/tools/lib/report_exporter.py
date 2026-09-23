"""
Suijin Report Exporter — sophisticated, detailed engagement reports.
Generates Markdown reports with Mermaid diagrams, finding tables, attack chains.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone


def _reports_dir():
    """reports dir (honours a monkeypatched module attr)."""
    v = globals().get("REPORTS_DIR")
    if v is not None:
        return v

    from suijin.modules.platform.lib.workspace import artifact_dir as _ad

    return _ad("reports")


def __getattr__(name):
    if name == "REPORTS_DIR":
        return _reports_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ensure_dir() -> None:
    """Create the reports dir on first write (no import-time side effects)."""
    _reports_dir().mkdir(parents=True, exist_ok=True)


def generate_report(
    engagement_name: str,
    execution_trace: list,
    findings: list,
    target_info: dict,
    messages: list,
    cost_usd: float = 0.0,
    completion_reason: str = "",
    attack_chains: list = None,
) -> str:
    """Generate a comprehensive engagement report in Markdown with Mermaid diagrams."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = engagement_name.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = _reports_dir() / f"{fname}_report_{ts}.md"
    _ensure_dir()
    report_dir = _reports_dir() / fname
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"data_{ts}.json"

    # Save full JSON data
    json_path.write_text(
        json.dumps(
            {
                "engagement": engagement_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "execution_trace": [dict(t) if hasattr(t, "items") else str(t) for t in execution_trace],
                "findings": findings,
                "target_info": target_info,
                "messages": messages,
                "cost_usd": cost_usd,
                "completion_reason": completion_reason,
            },
            indent=2,
            default=str,
        )
    )

    # Evidence join: the exploit catalog carries verification status +
    # POC receipts (commands + expected_result). A finding without a
    # CONFIRMED receipt is labeled UNVERIFIED — the report never asserts
    # more than the evidence shows (no LLM prose anywhere in this file).
    catalog = _catalog_receipts(engagement_name)

    # Build Markdown report
    lines = [
        "# Suijin Engagement Report",
        "",
        f"**Engagement**: {engagement_name}",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Completion**: {completion_reason or 'In progress'}",
        f"**Total Cost**: ${cost_usd:.4f}",
        f"**Total Steps**: {len(execution_trace)}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
    ]

    # Summary stats
    successful = sum(1 for t in execution_trace if t.get("success", True))
    tools_used = set(t.get("tool_name", "?") for t in execution_trace)
    lines.append(f"- {successful}/{len(execution_trace)} actions successful")
    lines.append(f"- Tools used: {', '.join(sorted(tools_used))}")
    lines.append(f"- Findings discovered: {len(findings)}")
    lines.append("")

    # Findings table
    if findings:
        lines.append("## Findings")
        lines.append("")
        lines.append("| # | Type | Severity | Endpoint | Status | Description |")
        lines.append("|---|------|----------|----------|--------|-------------|")
        for i, f in enumerate(findings, 1):
            sev = f.get("severity", "info").upper()
            ftype = f.get("type", "unknown")
            ep = f.get("endpoint", "?")
            desc = f.get("description", "")[:100]
            key = str(f.get("title") or f.get("rule") or desc)[:60].lower()
            status = catalog.get("by_title", {}).get(key, "UNVERIFIED")
            lines.append(f"| {i} | {ftype} | {sev} | {ep} | {status} | {desc} |")
        lines.append("")

        # Reproduction: exact commands + the expected marker per verified
        # finding — straight from the POC receipts
        reps = catalog.get("receipts", [])  # only CONFIRMED POCs are collected
        if reps:
            lines.append("### Reproduction (verified POCs)")
            lines.append("")
            for r in reps:
                lines.append(f"**{r['id']} — {r['title'][:80]}** ({r['severity']})")
                lines.append("")
                lines.append("```bash")
                lines.extend(r["commands"][:10])
                lines.append("```")
                lines.append(f"Expected result contains: `{r['expected'][:100]}`")
                lines.append("")

        # Remediation: deterministic per vuln class
        if findings:
            lines.append("### Remediation")
            lines.append("")
            for i, f in enumerate(findings, 1):
                cls = str(f.get("type") or f.get("finding_type") or "").lower()
                rem = _REMEDIATION.get(cls.split()[0] if cls else "", "")
                if rem:
                    lines.append(f"{i}. {rem}")
            lines.append("")

    # Attack chains with Mermaid diagram
    if attack_chains:
        lines.append("## Attack Chains")
        lines.append("")
        lines.append("```mermaid")
        lines.append("graph TD")
        for chain in attack_chains:
            steps = chain.get("steps", [])
            for j in range(len(steps) - 1):
                lines.append(f"    {_safe_id(steps[j])} --> {_safe_id(steps[j + 1])}")
        lines.append("```")
        lines.append("")

    # Full execution trace
    lines.append("## Full Execution Trace")
    lines.append("")
    for i, step in enumerate(execution_trace, 1):
        tn = step.get("tool_name", "none")
        thought = step.get("thought", "")[:200]
        success = "OK" if step.get("success", True) else "FAIL"
        lines.append(f"### Step {i}: {tn} [{success}]")
        lines.append(f"**Thought**: {thought}")
        reason = step.get("reasoning", "")
        if reason:
            lines.append(f"**Reasoning**: {reason[:300]}")
        args = step.get("tool_args", {})
        if args:
            lines.append(f"**Args**: `{json.dumps(args)[:200]}`")
        output = step.get("tool_output", "")
        if output:
            lines.append("**Output**:")
            lines.append("```")
            lines.append(output[:3000])
            lines.append("```")
        lines.append("")

    # Target intelligence
    if target_info:
        lines.append("## Target Intelligence")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(target_info, indent=2, default=str))
        lines.append("```")
        lines.append("")

    path.write_text("\n".join(lines))
    return str(path)


#: deterministic remediation guidance by vuln class — no LLM prose
_REMEDIATION = {
    "sqli": "Parameterize the query; never interpolate user input into SQL (OWASP A03).",
    "xss": "Contextual output encoding + a Content-Security-Policy without unsafe-inline.",
    "ssrf": "Allowlist outbound destinations at the egress layer; deny link-local and metadata ranges.",
    "source": "Strip source maps from production builds or gate them behind authentication.",
    "sourcemap": "Strip source maps from production builds or gate them behind authentication.",
    "auth": "Enforce server-side authorization on every route; do not trust client state.",
    "idor": "Scope every object access to the authenticated principal (OWASP A01).",
    "info": "Remove the informational exposure or gate it; disclose only what operations require.",
    "secret": "Rotate the exposed credential and remove it from the shipped artifact.",
    "rce": "Remove the execution sink; sandbox any remaining deserialization/command paths.",
}


def _catalog_receipts(engagement_name: str) -> dict:
    """Join the exploit catalog for THIS engagement: verification status
    by finding title + CONFIRMED POC receipts. Best-effort; missing
    catalog = every finding stays UNVERIFIED (honest, not empty)."""
    out: dict = {"by_title": {}, "receipts": []}
    with contextlib.suppress(Exception):
        import json as _json

        from suijin.modules.tools.lib.exploit_catalog import _catalog_roots, parse_exploit_yaml

        for root in _catalog_roots():
            idx = root / "catalog.json"
            if not idx.is_file():
                continue
            data = _json.loads(idx.read_text(encoding="utf-8"))
            entries = data.get("entries") or {}
            vals = entries.values() if isinstance(entries, dict) else entries
            for e in vals:
                if not isinstance(e, dict):
                    continue
                title = str(e.get("title") or "")[:60].lower()
                status = str(e.get("status") or "").upper()
                eid = str(e.get("id") or "?")
                sev = str(e.get("severity") or "?")
                cmds: list = []
                expected = ""
                yml = root / eid / "exploit.yaml"
                if yml.is_file():
                    cmds, expected = parse_exploit_yaml(yml.read_text(encoding="utf-8"))
                out["by_title"][title] = status or "UNVERIFIED"
                if status == "CONFIRMED":
                    out["receipts"].append(
                        {
                            "id": eid,
                            "title": str(e.get("title") or eid),
                            "severity": sev,
                            "commands": cmds,
                            "expected": expected,
                        }
                    )
    return out


def _safe_id(text: str) -> str:
    """Convert step text to safe Mermaid node ID."""
    return text.replace(" ", "_").replace("/", "_").replace("-", "_").replace(".", "_")[:30]
