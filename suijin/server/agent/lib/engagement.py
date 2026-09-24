"""
suijin/core/engagement.py — Engagement schema, validation, and session persistence.

Handles:
- Loading and validating engagement_schema.json
- Auto-save every N iterations
- Restore from checkpoint on startup
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# v4.1: engagement state is RUNTIME DATA — it lives in the agent
# workspace (the volume), never the package dir. Lazy accessors
# (boundary rule) that honour monkeypatched module attrs.
SCHEMA_PATH = None
RECOVERY_PATH = None


def _schema_path():
    v = globals().get("SCHEMA_PATH")
    if v is not None:
        return v  # monkeypatched / set by the operator
    from suijin.modules.platform.lib.workspace import engagement_dir

    return engagement_dir() / "schema.json"


def __getattr__(name):
    if name == "SCHEMA_PATH":
        return _schema_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    if not _schema_path().exists():
        schema = dict(DEFAULT_SCHEMA)
        schema["created_at"] = _utc_now()
        schema["updated_at"] = _utc_now()
        _schema_path().write_text(json.dumps(schema, indent=2))
        return schema
    try:
        return json.loads(_schema_path().read_text())
    except json.JSONDecodeError:
        logger.warning("Corrupt engagement schema — regenerating")
        _schema_path().write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        return dict(DEFAULT_SCHEMA)


def save_engagement_schema(schema: dict) -> None:
    """Persist the engagement schema to disk."""
    schema["updated_at"] = _utc_now()
    _schema_path().write_text(json.dumps(schema, indent=2, default=str))


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
    schema.setdefault("phases", {})["history"].append(
        {
            "from": old_phase,
            "to": new_phase,
            "at": _utc_now(),
        }
    )
    schema["phases"]["current"] = new_phase
    if old_phase not in schema["phases"].get("completed", []):
        schema["phases"].setdefault("completed", []).append(old_phase)
    save_engagement_schema(schema)


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
