"""HITL approvals — the operator console for human-in-the-loop mode.

When mode_hitl blocks a tool call, the blocked action is recorded as
PENDING in suijin_agent/approvals.json. The operator reviews with
`suijin approvals list` and either approves (the tool becomes allowed
for this session via approved_tools.json, consulted by modes.py) or
denies (harder block). Deliberately file-based and TTL-free: state is
one JSON file the operator can read, edit, or delete — no daemons, no
expiry race conditions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _approvals_path():
    """Approvals ledger path (honours a monkeypatched module attr)."""
    v = globals().get("APPROVALS_PATH")
    if v is not None:
        return v

    from suijin.modules.platform.lib.workspace import engagement_dir

    d = engagement_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "approvals.json"


def _session_path():
    v = globals().get("SESSION_PATH")
    if v is not None:
        return v
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "approved_tools.json"


def __getattr__(name):
    if name == "APPROVALS_PATH":
        return _approvals_path()
    if name == "SESSION_PATH":
        return _session_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_MAX_PENDING = 100


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def record_pending(tool: str, args: dict) -> int | None:
    """Log a HITL-blocked action. Returns its id, or None when disabled/failed.

    Never raises — approvals bookkeeping must not break tool dispatch.
    """
    try:
        data = _read(_approvals_path(), {"next_id": 1, "items": []})
        # skip if an identical pending item already exists
        for it in data["items"]:
            if it.get("status") == "pending" and it.get("tool") == tool and it.get("args") == _clip(args):
                return it["id"]
        item = {
            "id": data.get("next_id", 1),
            "tool": tool,
            "args": _clip(args),
            "blocked_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        data["items"].append(item)
        data["next_id"] = item["id"] + 1
        data["items"] = data["items"][-_MAX_PENDING:]
        _write(_approvals_path(), data)
        return item["id"]
    except Exception:
        return None


def _clip(args: dict) -> dict:
    return {k: (str(v)[:200]) for k, v in (args or {}).items()}


def list_approvals() -> list[dict]:
    return _read(_approvals_path(), {"items": []}).get("items", [])


def get_item(approval_id: int) -> dict | None:
    for it in list_approvals():
        if it.get("id") == approval_id:
            return it
    return None


def decision_for(tool: str) -> str:
    """ "approved" | "denied" | "none" — the session verdict for a tool."""
    sess = _read(_session_path(), {"approved": [], "denied": []})
    if tool in sess.get("denied", []):
        return "denied"
    if tool in sess.get("approved", []):
        return "approved"
    return "none"


def decide(approval_id: int, approve: bool, note: str = "") -> str:
    """Approve/deny a pending item; updates both files. Returns a message.

    note (v5.1): operator rationale recorded with the decision — the
    desktop gateway passes it from the Approvals card."""
    data = _read(_approvals_path(), {"next_id": 1, "items": []})
    item = next((i for i in data.get("items", []) if i.get("id") == approval_id), None)
    if item is None:
        return f"No approval request #{approval_id}."
    verb = "approved" if approve else "denied"
    item["status"] = verb
    if note:
        item["note"] = note[:300]
    item["decided_at"] = datetime.now(timezone.utc).isoformat()
    _write(_approvals_path(), data)

    sess = _read(_session_path(), {"approved": [], "denied": []})
    tool = item["tool"]
    if approve:
        sess.setdefault("approved", []).append(tool)
        if tool in sess.get("denied", []):
            sess["denied"].remove(tool)  # latest decision wins
    else:
        sess.setdefault("denied", []).append(tool)
        if tool in sess.get("approved", []):
            sess["approved"].remove(tool)
    _write(_session_path(), sess)
    what = "allowed for this session" if approve else "hard-blocked"
    return f"#{approval_id} ({tool}) {verb} — {what}."


def clear_session() -> str:
    """Wipe the session verdicts (fresh HITL stance); keeps the request log."""
    _session_path().unlink(missing_ok=True)
    return "session approvals cleared — HITL is back to recon-only."


def render_list() -> str:
    items = list_approvals()
    if not items:
        return (
            "No HITL blocks recorded. Approvals appear here when "
            "mode_hitl blocks a tool call.\nEnable: suijin config -> mode_hitl: true"
        )
    lines = [f"{len(items)} approval request(s) (newest last):"]
    for it in items:
        mark = {"pending": "[..]", "approved": "[ok]", "denied": "[NO]"}[it["status"]]
        args = " ".join(f"{k}={v}" for k, v in (it.get("args") or {}).items())[:80]
        lines.append(f"  {mark} #{it['id']:>3} {it['tool']:20} {str(it.get('blocked_at', ''))[:19]} {args}")
    lines.append("\nDecide: suijin approvals approve <id> | deny <id> | clear")
    return "\n".join(lines)
