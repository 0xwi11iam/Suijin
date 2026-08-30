"""BF7 — learning: FP allowlist, LLM rule drafting, playbook outcomes.

FP allowlist: /fp <pattern> marks a signal as false positive → the feed
fast path checks the allowlist BEFORE the detector (allowlist_check was
dead code — now it's the first thing the pattern check does).

Rule drafting: after a miss or FP, the AI drafts a detector rule
(pattern regex + weight + reason), stored for operator promotion to
detector_rules.json via `suijin blue promote`.

Playbook outcomes: which playbook was invoked per case + its result,
rendered in the session report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def _draft_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "outputs" / "blue_state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "draft_rules.json"


def _detector_rules_path() -> Path:
    from suijin.modules.platform.lib.runtime import _PKG_DIR

    return Path(_PKG_DIR) / "detector_rules.json"


# ── FP allowlist (wire the graveyard ops.py E36/E37 into the fast path) ──


def _allowlist_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "outputs" / "blue_state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "allowlist.json"


def fp_allowlist_add(pattern: str, reason: str = "") -> None:
    """Mark a signal/pattern as false positive — the feed checks this
    BEFORE the detector fires."""
    try:
        p = _allowlist_path()
        data = json.loads(p.read_text()) if p.exists() else {"patterns": []}
        entry = {"pattern": pattern, "reason": reason[:200]}
        if not any(e["pattern"] == pattern for e in data["patterns"]):
            data["patterns"].append(entry)
        p.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def fp_allowlist_check(signal_name: str, path: str = "", query: str = "") -> bool:
    """True when this signal on this path/query is allowlisted — the
    detector should NOT fire."""
    try:
        p = _allowlist_path()
        if not p.exists():
            return False
        data = json.loads(p.read_text())
        for e in data.get("patterns", []):
            pat = e.get("pattern", "")
            if not pat:
                continue
            if pat == signal_name:
                return True  # signal-level allowlist
            # path-level: pattern matches the URL path
            try:
                if path and re.search(pat, path):
                    return True
            except re.error:
                continue
    except (OSError, json.JSONDecodeError):
        pass
    return False


# ── LLM rule drafting (dry-run → operator promote) ──────────────────────


def draft_rule(pattern: str, weight: int, reason: str, attack_type: str = "custom") -> dict:
    """The AI drafts a detector rule from a miss or FP. Stored for
    operator review — never auto-applied."""
    try:
        p = _draft_path()
        data = json.loads(p.read_text()) if p.exists() else {"rules": [], "next_id": 1}
        rid = data.get("next_id", 1)
        data["next_id"] = rid + 1
        entry = {
            "id": f"DRAFT-{rid:03d}",
            "pattern": pattern,
            "weight": max(1, min(10, weight)),
            "reason": reason[:300],
            "attack_type": attack_type,
            "status": "draft",
        }
        data["rules"].append(entry)
        p.write_text(json.dumps(data, indent=2))
        return entry
    except OSError:
        return {"error": "could not save draft"}


def list_drafts() -> list[dict]:
    try:
        p = _draft_path()
        if not p.exists():
            return []
        return json.loads(p.read_text()).get("rules", [])
    except (OSError, json.JSONDecodeError):
        return []


def promote_draft(draft_id: str) -> dict:
    """Promote a draft rule to the live detector_rules.json."""
    try:
        p = _draft_path()
        data = json.loads(p.read_text()) if p.exists() else {"rules": []}
        draft = next((r for r in data["rules"] if r.get("id") == draft_id and r.get("status") == "draft"), None)
        if draft is None:
            return {"error": f"draft {draft_id} not found or already promoted"}

        # append to the live rules file
        rules_path = _detector_rules_path()
        live = json.loads(rules_path.read_text()) if rules_path.exists() else []
        live.append(
            {
                "pattern": draft["pattern"],
                "weight": draft["weight"],
                "attack_type": draft.get("attack_type", "custom"),
                "reason": draft.get("reason", ""),
            }
        )
        rules_path.write_text(json.dumps(live, indent=2))

        # mark the draft promoted
        draft["status"] = "promoted"
        p.write_text(json.dumps(data, indent=2))
        return {"promoted": draft_id, "pattern": draft["pattern"], "weight": draft["weight"]}
    except (OSError, json.JSONDecodeError) as e:
        return {"error": str(e)}


# ── Playbook outcomes ────────────────────────────────────────────────────


def record_playbook_outcome(case_id: str, playbook: str, outcome: str, case_store) -> None:
    """Record which playbook ran for a case and what happened."""
    case = case_store._index["cases"].get(case_id)
    if case is None:
        return
    case.setdefault("timeline", []).append(
        {"ts": _now(), "kind": "playbook", "type": playbook, "detail": outcome[:200]}
    )
    case_store._save(case, case_id)


def playbook_effectiveness(case_store) -> dict:
    """Aggregate playbook outcomes across all cases for the session report."""
    stats: dict = {}
    for case in case_store._index["cases"].values():
        for e in case.get("timeline", []):
            if e.get("kind") == "playbook":
                pb = e.get("type", "?")
                if pb not in stats:
                    stats[pb] = {"invoked": 0, "effective": 0, "ineffective": 0}
                stats[pb]["invoked"] += 1
                detail = e.get("detail", "").lower()
                if any(w in detail for w in ("contained", "blocked", "succeeded", "detected", "worked")):
                    stats[pb]["effective"] += 1
                elif any(w in detail for w in ("missed", "failed", "no effect", "bypassed")):
                    stats[pb]["ineffective"] += 1
    return stats


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
