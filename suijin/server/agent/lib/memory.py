"""Long-term engagement memory (B11) + target delta (B16) + drift (B17).

Cross-engagement memory beyond the knowledge graph: what was tried per
target, operator preferences, and what changed between engagements.
Lives in the workspace (outputs/memory/), survives everything, and is
explicitly NOT the KG (which stores verified constraints; this stores
operational experience).
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _mem_dir() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("memory")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _target_file(target: str) -> Path:
    safe = "".join(c for c in target if c.isalnum() or c in ".-_")[:60] or "unknown"
    return _mem_dir() / f"{safe}.json"


def record_engagement(target: str, objective: str, outcome: dict | None = None) -> None:
    """Append an engagement record to the target's memory."""
    f = _target_file(target)
    data = (
        json.loads(f.read_text())
        if f.exists()
        else {"target": target, "engagements": [], "fingerprints": [], "operator_notes": []}
    )
    data["engagements"].append(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "objective": objective[:200],
            "outcome": outcome or {},
        }
    )
    data["engagements"] = data["engagements"][-20:]  # bounded
    f.write_text(json.dumps(data, indent=2))


def record_fingerprint(target: str, fingerprint: dict) -> bool:
    """Store a target fingerprint; returns True when it CHANGED vs the
    last stored one (B16 delta / B17 drift share this)."""
    f = _target_file(target)
    data = (
        json.loads(f.read_text())
        if f.exists()
        else {"target": target, "engagements": [], "fingerprints": [], "operator_notes": []}
    )
    fp = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "data": fingerprint}
    changed = bool(data["fingerprints"]) and data["fingerprints"][-1].get("data") != fingerprint
    data["fingerprints"].append(fp)
    data["fingerprints"] = data["fingerprints"][-10:]
    f.write_text(json.dumps(data, indent=2))
    return changed


def delta(target: str, fingerprint: dict) -> str:
    """B16: human diff of a fingerprint vs the last stored one."""
    f = _target_file(target)
    if not f.exists():
        return "no prior fingerprint — this one becomes the baseline"
    data = json.loads(f.read_text())
    if not data.get("fingerprints"):
        return "no prior fingerprint — this one becomes the baseline"
    prev = data["fingerprints"][-1].get("data") or {}
    changes = []
    for k in sorted(set(prev) | set(fingerprint)):
        a, b = prev.get(k), fingerprint.get(k)
        if a != b:
            changes.append(f"  {k}: {str(a)[:40]!r} -> {str(b)[:40]!r}")
    if not changes:
        return "target unchanged since last engagement"
    return f"TARGET DELTA ({len(changes)} change(s)):\n" + "\n".join(changes)


def recall(target: str, limit: int = 5) -> str:
    """What we know operationally about this target."""
    f = _target_file(target)
    if not f.exists():
        return f"no memory of {target} yet — first engagement against it"
    data = json.loads(f.read_text())
    lines = [f"{target}: {len(data['engagements'])} prior engagement(s)"]
    for e in data["engagements"][-limit:]:
        oc = e.get("outcome") or {}
        lines.append(
            f"  {e['ts'][:10]} {e['objective'][:60]}"
            + (f" -> {str(oc.get('completion_reason', '?'))[:30]}" if oc else "")
        )
    for note in (data.get("operator_notes") or [])[-3:]:
        lines.append(f"  note: {note[:80]}")
    return "\n".join(lines)


def note(target: str, text: str) -> None:
    f = _target_file(target)
    data = (
        json.loads(f.read_text())
        if f.exists()
        else {"target": target, "engagements": [], "fingerprints": [], "operator_notes": []}
    )
    data.setdefault("operator_notes", []).append(text[:300])
    data["operator_notes"] = data["operator_notes"][-20:]
    f.write_text(json.dumps(data, indent=2))
