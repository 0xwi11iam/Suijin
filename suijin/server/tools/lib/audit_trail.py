"""
Suijin Audit Trail — complete, zero-truncation JSON/MD logging.
Records: what the AI saw (tool outputs), thought (reasoning), did (actions).

Write cadence (2026-09-26, the long-run lag fix): the trail used to be
rewritten IN FULL on every log_iteration/log_finding call. Observations
are stored untruncated by design, so on a long engagement the file grows
into megabytes and every tool call then paid a GIL-held json.dumps of the
whole history — the TUI's typewriter and input threads starved and the
console became unusable to type in. _save() is now throttled: dirty-flag
plus a forced flush at end/iteration boundaries. The events.jsonl journal
remains the durable per-event record, so a crash at most loses the tail
of a report copy — never the run itself.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

#: minimum seconds between disk writes for throttled saves
FLUSH_INTERVAL_S = 10.0
_last_flush = 0.0
_dirty = False


def _audit_dir():
    """audit_trails dir (honours a monkeypatched module attr)."""
    v = globals().get("AUDIT_DIR")
    if v is not None:
        return v

    from suijin.modules.platform.lib.workspace import artifact_dir as _ad

    return _ad("audit_trails")


def __getattr__(name):
    if name == "AUDIT_DIR":
        return _audit_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ensure_dir() -> None:
    """Create the audit dir on first write (no import-time side effects)."""
    _audit_dir().mkdir(parents=True, exist_ok=True)


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
    _save(force=True)


def flush():
    """Persist the trail if it is stale (interval-guarded, cheap no-op).

    Called at iteration boundaries so a mid-run reader (dossier, report)
    never sees data older than FLUSH_INTERVAL_S. The end of the run forces
    a final write in end_audit; the events.jsonl journal remains the
    durable per-event record, so a crash loses at most the tail of this
    REPORT copy — never the run itself."""
    global _dirty
    if _current_trail is None:
        return
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
    _save(force=True)  # the end MUST land — this is the report of record
    path = _export_markdown()
    _current_trail = None
    return path


def _save(force: bool = False):
    """Persist the trail — throttled unless forced.

    `force=True` at engagement end, iteration boundaries and pause/ask
    points; ordinary tool-call logging only marks dirty and writes at
    most once per FLUSH_INTERVAL_S. See the module docstring for why.
    """
    global _last_flush, _dirty
    if _current_trail is None:
        return
    now = time.monotonic()
    if not force and now - _last_flush < FLUSH_INTERVAL_S:
        _dirty = True  # write it on the next forced/eligible flush
        return
    fname = _current_engagement.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    _ensure_dir()
    path = _audit_dir() / f"{fname}.json"
    # compact JSON: ~30% smaller and several times faster to serialize
    # than indent=2 — the indent was pure formatting cost on a file only
    # tools read
    path.write_text(json.dumps(_current_trail, separators=(",", ":"), default=str))
    _last_flush = now
    _dirty = False


def _export_markdown() -> str:
    if _current_trail is None:
        return ""
    _ensure_dir()
    fname = _current_engagement.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = _audit_dir() / f"{fname}.md"
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
