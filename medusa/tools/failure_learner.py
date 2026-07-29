"""Learning from failure — record blocked patterns and never repeat them."""
import json, os, hashlib
from pathlib import Path
from datetime import datetime, timezone
FAILURE_DB = Path(__file__).resolve().parent.parent / "medusa_agent" / "failure_db.json"

def record_failure(target: str, technique: str, payload: str, reason: str):
    failures = json.loads(FAILURE_DB.read_text()) if FAILURE_DB.exists() else []
    key = hashlib.sha256(f"{target}:{technique}:{payload[:50]}".encode()).hexdigest()[:12]
    failures.append({"id": key, "target": target, "technique": technique,
                     "payload_snippet": payload[:100], "reason": reason[:200],
                     "timestamp": datetime.now(timezone.utc).isoformat(), "times_seen": 1})
    # Deduplicate
    seen = {}
    for f in failures:
        k = f["id"]
        if k in seen: seen[k]["times_seen"] += 1
        else: seen[k] = f
    FAILURE_DB.write_text(json.dumps(list(seen.values()), indent=2))
    return f"Recorded failure: {technique} on {target} — {reason[:80]}"

def should_skip(target: str, technique: str) -> bool:
    if not FAILURE_DB.exists(): return False
    failures = json.loads(FAILURE_DB.read_text())
    for f in failures:
        if f["target"] == target and f["technique"] == technique and f.get("times_seen", 0) >= 3:
            return True
    return False

def get_learned_patterns(target: str) -> list:
    if not FAILURE_DB.exists(): return []
    return [f for f in json.loads(FAILURE_DB.read_text()) if f["target"] == target]
