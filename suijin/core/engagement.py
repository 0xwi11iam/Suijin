"""
suijin/core/engagement.py — Engagement schema, validation, and session persistence.

Handles:
- Loading and validating engagement_schema.json
- Session save/restore for crash recovery (operation_state_recovery.json)
- Auto-save every N iterations
- Restore from checkpoint on startup
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent  # suijin/
SCHEMA_PATH = BASE_DIR / "engagement_schema.json"
RECOVERY_PATH = BASE_DIR / "operation_state_recovery.json"

# Default schema template
DEFAULT_SCHEMA = {
    "_schema": "suijin-engagement-v2",
    "objective": "",
    "created_at": "",
    "updated_at": "",
    "targets": {
        "primary": [],
        "secondary": [],
        "out_of_scope": [],
    },
    "scope": {
        "allowed_ports": [],
        "allowed_services": ["http", "https", "ssh"],
        "max_scan_rate": 1000,
        "allowed_techniques": ["recon", "fuzzing", "exploit", "post_exploit"],
        "excluded_techniques": ["ddos", "social_engineering"],
    },
    "phases": {
        "current": "recon",
        "completed": [],
        "history": [],
    },
    "findings": [],
    "credentials_file": "suijin_agent/credentials.json",
    "notes_file": "suijin_agent/.notes",
    "stats": {
        "requests_sent": 0,
        "endpoints_discovered": 0,
        "vulnerabilities_found": 0,
        "flags_captured": 0,
        "cost_usd": 0.0,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_engagement_schema() -> dict:
    """Load the engagement schema, creating it if missing."""
    if not SCHEMA_PATH.exists():
        schema = dict(DEFAULT_SCHEMA)
        schema["created_at"] = _utc_now()
        schema["updated_at"] = _utc_now()
        SCHEMA_PATH.write_text(json.dumps(schema, indent=2))
        return schema
    try:
        return json.loads(SCHEMA_PATH.read_text())
    except json.JSONDecodeError:
        logger.warning("Corrupt engagement schema — regenerating")
        SCHEMA_PATH.write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        return dict(DEFAULT_SCHEMA)


def save_engagement_schema(schema: dict) -> None:
    """Persist the engagement schema to disk."""
    schema["updated_at"] = _utc_now()
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2, default=str))


def update_engagement_stats(**kwargs) -> None:
    """Update stats counters in the engagement schema."""
    schema = load_engagement_schema()
    stats = schema.setdefault("stats", {})
    for key, delta in kwargs.items():
        stats[key] = stats.get(key, 0) + delta
    save_engagement_schema(schema)


def add_finding_to_schema(finding: dict) -> None:
    """Add a finding to the engagement schema."""
    schema = load_engagement_schema()
    finding.setdefault("timestamp", _utc_now())
    schema.setdefault("findings", []).append(finding)
    stats = schema.setdefault("stats", {})
    stats["vulnerabilities_found"] = stats.get("vulnerabilities_found", 0) + 1
    save_engagement_schema(schema)


def transition_phase(new_phase: str) -> None:
    """Record a phase transition in the engagement schema."""
    schema = load_engagement_schema()
    old_phase = schema.get("phases", {}).get("current", "recon")
    schema.setdefault("phases", {})["history"].append({
        "from": old_phase,
        "to": new_phase,
        "at": _utc_now(),
    })
    schema["phases"]["current"] = new_phase
    if old_phase not in schema["phases"].get("completed", []):
        schema["phases"].setdefault("completed", []).append(old_phase)
    save_engagement_schema(schema)


# ── Session Recovery ────────────────────────────────────────────────────────

def save_session_state(state: dict) -> str:
    """Save full agent state for crash recovery. Returns the file path."""
    recovery_data = {
        "_recovery_version": "2.0",
        "saved_at": _utc_now(),
        "objective": state.get("original_objective", ""),
        "phase": state.get("current_phase", "informational"),
        "iteration": state.get("current_iteration", 0),
        "cost_usd": state.get("total_cost_usd", 0.0),
        "findings": state.get("findings", []),
        "flags_found": state.get("flags_found", []),
        "messages": state.get("messages", [])[-20:],  # last 20 messages
        "execution_trace": _serialize_trace(state.get("execution_trace", [])),
        "todo_list": state.get("todo_list", []),
        "knowledge_graph_snapshot": state.get("knowledge_graph", {}),
        "chain_findings_memory": state.get("chain_findings_memory", []),
        "audit_trail": state.get("audit_trail", [])[-50:],
        "target_info": _serialize_target(state.get("target_info", {})),
        "engagement_stats": load_engagement_schema().get("stats", {}),
    }
    RECOVERY_PATH.write_text(json.dumps(recovery_data, indent=2, default=str))
    return str(RECOVERY_PATH)


def load_session_state() -> Optional[dict]:
    """Load a previously saved session for recovery. Returns None if no save exists."""
    if not RECOVERY_PATH.exists():
        return None
    try:
        data = json.loads(RECOVERY_PATH.read_text())
    except json.JSONDecodeError:
        logger.warning("Corrupt recovery file — ignoring")
        return None
    if not data.get("objective"):
        return None
    logger.info(
        "Recovery state found: phase=%s iteration=%s saved=%s",
        data.get("phase"), data.get("iteration"), data.get("saved_at"),
    )
    return data


def clear_recovery_state() -> None:
    """Remove the recovery file after successful completion."""
    if RECOVERY_PATH.exists():
        RECOVERY_PATH.unlink()
        logger.info("Recovery state cleared — engagement completed normally")


def has_recovery_state() -> bool:
    """Check if a recovery save exists."""
    return RECOVERY_PATH.exists() and RECOVERY_PATH.stat().st_size > 0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _serialize_trace(trace: list) -> list:
    """Convert execution trace entries to JSON-serializable dicts."""
    result = []
    for entry in trace[-30:]:  # Keep last 30 entries
        if hasattr(entry, "model_dump"):
            result.append(entry.model_dump())
        elif isinstance(entry, dict):
            result.append(entry)
        else:
            result.append({"raw": str(entry)[:500]})
    return result


def _serialize_target(target_info) -> dict:
    """Serialize target info to a plain dict."""
    if target_info is None:
        return {}
    if hasattr(target_info, "model_dump"):
        return target_info.model_dump()
    if isinstance(target_info, dict):
        return target_info
    return {"raw": str(target_info)[:500]}
