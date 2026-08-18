"""
Suijin Audit Trail — complete, zero-truncation JSON/MD logging.
Records: what the AI saw (tool outputs), thought (reasoning), did (actions).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from suijin.tools.workspace import WORKSPACE_DIR

AUDIT_DIR = WORKSPACE_DIR / "audit_trails"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

_current_trail = None
_current_engagement = "unknown"


def start_audit(engagement_name: str):
    global _current_trail, _current_engagement
    _current_engagement = engagement_name or "unnamed"
    _current_trail = {
        "engagement": _current_engagement,
        "started": datetime.now(timezone.utc).isoformat(),
        "ended": None,
        "iterations": [],
        "findings": [],
        "total_actions": 0,
        "successful_actions": 0,
        "failed_actions": 0,
        "cost_usd": 0.0,
    }
    _save()


def log_iteration(
    iteration: int,
    thought: str,
    reasoning: str,
    tool_name: str,
    tool_args: dict,
    tool_output: str,
    success: bool,
    phase: str,
    completion_reason: str = "",
    chain_context: str = "",
):
    global _current_trail
    if _current_trail is None:
        return
    entry = {
        "iteration": iteration,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "thought": thought,
        "reasoning": reasoning,
        "action": {
            "tool": tool_name or "none",
            "args": tool_args,
            "success": success,
        },
        "observation": tool_output,  # FULL output, zero truncation
        "chain_context": chain_context,
        "completion_reason": completion_reason,
    }
    _current_trail["iterations"].append(entry)
    _current_trail["total_actions"] += 1
    if success:
        _current_trail["successful_actions"] += 1
    else:
        _current_trail["failed_actions"] += 1
    _save()


def log_finding(finding_type: str, severity: str, endpoint: str, description: str, evidence: str):
    global _current_trail
    if _current_trail is None:
        return
    _current_trail["findings"].append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": finding_type,
            "severity": severity,
            "endpoint": endpoint,
            "description": description,
            "evidence": evidence,
        }
    )
    _save()


def end_audit(cost_usd: float = 0.0):
    global _current_trail
    if _current_trail is None:
        return
    _current_trail["ended"] = datetime.now(timezone.utc).isoformat()
    _current_trail["cost_usd"] = cost_usd
    _save()
    path = _export_markdown()
    _current_trail = None
    return path


def _save():
    if _current_trail is None:
        return
    fname = _current_engagement.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = AUDIT_DIR / f"{fname}.json"
    path.write_text(json.dumps(_current_trail, indent=2, default=str))


def _export_markdown() -> str:
    if _current_trail is None:
        return ""
    fname = _current_engagement.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = AUDIT_DIR / f"{fname}.md"
    t = _current_trail
    lines = [
        f"# Suijin Audit Trail — {t['engagement']}",
        "",
        f"**Started**: {t['started']}",
        f"**Ended**: {t['ended']}",
        f"**Actions**: {t['total_actions']} total ({t['successful_actions']} success, {t['failed_actions']} failed)",
        f"**Cost**: ${t['cost_usd']:.4f}",
        f"**Findings**: {len(t['findings'])}",
        "",
        "## Findings",
    ]
    for f in t.get("findings", []):
        lines.append(f"- **{f['severity'].upper()}** [{f['type']}] {f['endpoint']}: {f['description']}")
        if f.get("evidence"):
            lines.append(f"  Evidence: {f['evidence'][:200]}")

    lines.append("")
    lines.append("## Full Iteration Log")
    for _i, it in enumerate(t.get("iterations", []), 1):
        lines.append(f"### #{it['iteration']} [{it['phase']}] {it['action']['tool']}")
        lines.append(f"**Thought**: {it['thought']}")
        lines.append(f"**Reasoning**: {it['reasoning']}")
        if it.get("completion_reason"):
            lines.append(f"**Completion**: {it['completion_reason']}")
        lines.append(f"**Tool**: {it['action']['tool']} — {'SUCCESS' if it['action']['success'] else 'FAILED'}")
        lines.append(f"**Args**: `{json.dumps(it['action']['args'])}`")
        lines.append("")
        lines.append("**Full Output**:")
        lines.append("```")
        lines.append(it["observation"][:10000])  # Cap per-iteration at 10K in MD
        lines.append("```")
        lines.append("")

    path.write_text("\n".join(lines))
    return str(path)


def get_audit_json() -> dict:
    return _current_trail or {}
