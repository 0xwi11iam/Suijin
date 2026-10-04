"""
Suijin Audit Trail — complete, zero-truncation JSON/MD logging.
Records: what the AI saw (tool outputs), thought (reasoning), did (actions).

2026-10-04 rewrite for multi-agent sessions: the trail used MODULE GLOBALS
for the current engagement, so 4 terminals in one process shared a single
dict — the last start_audit() won and 3 agents' trails never landed. The
trail is now keyed BY ENGAGEMENT DIR (the same workspace seam agent_steps
uses): each engagement's trail lives in its own audit_trails/ dir, writes
independently, and never touches another engagement's file.

Write cadence preserved from the 2026-09-26 fix: dirty-flag + interval
guard (10s), forced flush at iteration boundaries and engagement end. The
events.jsonl journal remains the durable per-event record.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path as _P

#: minimum seconds between disk writes for throttled saves
FLUSH_INTERVAL_S = 10.0

#: per-engagement trail state: {engagement_key: trail_dict}
#: _locks guards the dict itself; each trail's writes are single-threaded
#: (called from the engagement's own loop), so no per-trail lock needed
_trails: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}
_meta_lock = threading.Lock()

#: last flush time per engagement (the throttle)
_last_flush: dict[str, float] = {}

_overridden_dir: _P | None = None  # tests pin a single dir (legacy)
_overridden_key: str | None = None  # tests pin a per-test engagement key


def _engagement_key() -> str:
    """Stable key for THIS engagement's trail — the workspace's current
    engagement dir (same seam agent_steps uses). Falls back to the
    engagement name when no workspace engagement is set."""
    if _overridden_key is not None:
        return _overridden_key
    from suijin.modules.platform.lib.workspace import engagement_dir

    eng = engagement_dir()
    if eng is not None and eng.parent.name != "_default":
        return str(eng)
    return "unnamed_engagement"


def _audit_dir(key: str | None = None) -> _P:
    """audit_trails dir for this engagement (or the override for tests)."""
    if _overridden_dir is not None:
        return _overridden_dir
    from suijin.modules.platform.lib.workspace import engagement_dir

    eng = engagement_dir()
    base = _P(eng) if eng is not None else _P.home() / ".suijin" / "workspace"
    d = base / "audit_trails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_audit_dir(path, key: str | None = None) -> None:
    """Tests pin a directory and optionally a per-test engagement key;
    None restores the workspace seam."""
    global _overridden_dir, _overridden_key
    _overridden_dir = _P(path) if path else None
    _overridden_key = key


def _fname_for(key: str) -> str:
    """File-safe name from the engagement key (dir path → last component,
    or the name itself if not a path)."""
    name = key.rstrip("/").split("/")[-1] if "/" in key else key
    return name.replace("/", "_").replace(" ", "_").replace(":", "_")[:60]


# ── lifecycle ────────────────────────────────────────────────────────────


def start_audit(engagement_name: str):
    """Begin a trail for THIS engagement. Multiple concurrent engagements
    each get their own — the last caller no longer clobbers the others."""
    key = _engagement_key()
    with _meta_lock:
        _locks.setdefault(key, threading.Lock())
        _trails[key] = {
            "engagement": engagement_name or "unnamed",
            "key": key,
            "started": datetime.now(timezone.utc).isoformat(),
            "ended": None,
            "iterations": [],
            "findings": [],
            "total_actions": 0,
            "successful_actions": 0,
            "failed_actions": 0,
            "cost_usd": 0.0,
        }
        _last_flush[key] = 0.0
    _save(key, force=True)


def flush():
    """Persist THIS engagement's trail if stale. Called at iteration
    boundaries so a mid-run reader never sees data older than
    FLUSH_INTERVAL_S. Cheap no-op when clean."""
    key = _engagement_key()
    if key in _trails:
        _save(key)


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
    key = _engagement_key()
    trail = _trails.get(key)
    if trail is None:
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
    with _locks.get(key, _meta_lock):
        trail["iterations"].append(entry)
        trail["total_actions"] += 1
        if success:
            trail["successful_actions"] += 1
        else:
            trail["failed_actions"] += 1
    # COUNT-BASED FORCE (the operator's 4-agent run 2026-10-04: short
    # engagements finished inside the 10-second throttle window, so the
    # trail JSON stayed empty while agent_steps.jsonl recorded fine —
    # the boundary flushes hit the time guard and skipped). Every 5th
    # iteration writes regardless of elapsed time.
    if len(trail["iterations"]) % 5 == 0:
        _save(key, force=True)
    else:
        _save(key)


def log_finding(finding_type: str, severity: str, endpoint: str, description: str, evidence: str):
    key = _engagement_key()
    trail = _trails.get(key)
    if trail is None:
        return
    with _locks.get(key, _meta_lock):
        trail["findings"].append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": finding_type,
                "severity": severity,
                "endpoint": endpoint,
                "description": description,
                "evidence": evidence,
            }
        )
    _save(key)


def end_audit(cost_usd: float = 0.0):
    """Conclude THIS engagement's trail — forced write + markdown export.
    Other engagements' trails are untouched."""
    key = _engagement_key()
    trail = _trails.get(key)
    if trail is None:
        return None
    trail["ended"] = datetime.now(timezone.utc).isoformat()
    trail["cost_usd"] = cost_usd
    _save(key, force=True)  # the end MUST land — this is the report of record
    path = _export_markdown(key)
    with _meta_lock:
        _trails.pop(key, None)
        _locks.pop(key, None)
        _last_flush.pop(key, None)
    return path


# ── persistence ──────────────────────────────────────────────────────────


def _save(key: str, force: bool = False):
    """Persist one engagement's trail — throttled unless forced.

    `force=True` at engagement end, iteration boundaries and pause/ask
    points; ordinary tool-call logging only marks dirty and writes at
    most once per FLUSH_INTERVAL_S per engagement (the 2026-09-26 fix,
    now per-key so 4 concurrent agents don't share a throttle clock
    either)."""
    trail = _trails.get(key)
    if trail is None:
        return
    now = time.monotonic()
    if not force and now - _last_flush.get(key, 0.0) < FLUSH_INTERVAL_S:
        return  # dirty — the next forced/eligible flush writes it
    # the FILENAME is the engagement NAME (what start_audit received) —
    # same as the old behavior so readers/dossiers find it
    fname = str(trail.get("engagement", "unnamed")).replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = _audit_dir(key) / f"{fname}.json"
    path.write_text(json.dumps(trail, separators=(",", ":"), default=str))
    with _meta_lock:
        _last_flush[key] = now


def _export_markdown(key: str) -> str:
    trail = _trails.get(key)
    if trail is None:
        return ""
    fname = str(trail.get("engagement", "unnamed")).replace("/", "_").replace(" ", "_").replace(":", "_")[:60]
    path = _audit_dir(key) / f"{fname}.md"
    t = trail
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
    """THIS engagement's trail (read-only copy for the report path)."""
    key = _engagement_key()
    trail = _trails.get(key)
    return dict(trail) if trail else {}


def reset_all() -> None:
    """Tests: clear every trail state."""
    with _meta_lock:
        _trails.clear()
        _locks.clear()
        _last_flush.clear()


#: legacy compat: the old module exposed _dirty as a module global. The new
#: design has no single dirty flag (it's per-engagement). This always
#: returns False — old tests that asserted _dirty semantics are updated.
_dirty = False


def __getattr__(name):
    """Legacy module attribute compatibility."""
    if name == "AUDIT_DIR":
        return _audit_dir()  # resolve the current audit dir
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
