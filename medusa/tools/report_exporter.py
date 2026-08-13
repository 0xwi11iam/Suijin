"""
Medusa Report Exporter — sophisticated, detailed engagement reports.
Generates Markdown reports with Mermaid diagrams, finding tables, attack chains.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path
from datetime import datetime, timezone

REPORTS_DIR = Path(__file__).resolve().parent.parent / "medusa_agent" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def generate_report(engagement_name: str, execution_trace: list, findings: list,
                    target_info: dict, messages: list, cost_usd: float = 0.0,
                    completion_reason: str = "", attack_chains: list = None) -> str:
    """Generate a comprehensive engagement report in Markdown with Mermaid diagrams."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = engagement_name.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = REPORTS_DIR / f"{fname}_report_{ts}.md"
    report_dir = REPORTS_DIR / fname
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"data_{ts}.json"

    # Save full JSON data
    json_path.write_text(json.dumps({
        "engagement": engagement_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_trace": [dict(t) if hasattr(t, 'items') else str(t) for t in execution_trace],
        "findings": findings,
        "target_info": target_info,
        "messages": messages,
        "cost_usd": cost_usd,
        "completion_reason": completion_reason,
    }, indent=2, default=str))

    # Build Markdown report
    lines = [
        f"# Medusa Engagement Report",
        f"",
        f"**Engagement**: {engagement_name}",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Completion**: {completion_reason or 'In progress'}",
        f"**Total Cost**: ${cost_usd:.4f}",
        f"**Total Steps**: {len(execution_trace)}",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
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
        lines.append("| # | Type | Severity | Endpoint | Description |")
        lines.append("|---|------|----------|----------|-------------|")
        for i, f in enumerate(findings, 1):
            sev = f.get("severity", "info").upper()
            ftype = f.get("type", "unknown")
            ep = f.get("endpoint", "?")
            desc = f.get("description", "")[:100]
            lines.append(f"| {i} | {ftype} | {sev} | {ep} | {desc} |")
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
                lines.append(f"    {_safe_id(steps[j])} --> {_safe_id(steps[j+1])}")
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
            lines.append(f"**Output**:")
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


def _safe_id(text: str) -> str:
    """Convert step text to safe Mermaid node ID."""
    return text.replace(" ", "_").replace("/", "_").replace("-", "_").replace(".", "_")[:30]
