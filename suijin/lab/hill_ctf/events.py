"""Hill CTF events — typed signals every stage crossing emits.

Blue's detection surface: richer than the plain traffic log. Each event
lands in <lab_dir>/hill_events.jsonl (one JSON per line) AND the
standard blue traffic JSONL keeps flowing from app.py. Watchers key on
these types (BF2); the hill_defense.json levers (defenses.py) can mute
the noisier ones.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
EVENTS_PATH = Path(os.environ.get("HILL_EVENTS_LOG", str(LAB_DIR / "hill_events.jsonl")))

_lock = threading.Lock()

# severity guides the (future) watcher fast-path: critical = auto-enforce
EVENT_TYPES = {
    "decoy_hit": "high",  # touched a canary-wired decoy
    "canary_used": "critical",  # canary credential used anywhere
    "auth_fail": "low",  # single login failure (velocity matters)
    "auth_fail_velocity": "high",
    "jwt_tamper": "high",  # alg confusion / bad signature
    "idor_access": "high",  # docs read across users
    "ssrf_attempt": "high",  # webhook fetch tried
    "ssrf_blocked": "medium",  # blocklist caught it
    "metadata_access": "critical",  # internal metadata reached
    "canary_metadata": "critical",  # CANARY creds at metadata = trip
    "internal_api": "high",  # internal API called with temp creds
    "vault_access": "critical",  # vault route touched
    "vault_decrypt": "critical",  # decryptor invoked (final step)
    "token_rotated": "info",  # rotation heartbeat / force-rotate
    "token_validated": "low",  # red checking freshness
    "rate_limited": "medium",
}


def emit(event_type: str, **fields) -> None:
    """Append one typed event. Never raises (the lab must not break)."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": event_type,
            "severity": EVENT_TYPES.get(event_type, "info"),
            **fields,
        }
        with _lock:
            EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with EVENTS_PATH.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def recent(limit: int = 50) -> list:
    """Read back recent events (admin/debug surface)."""
    try:
        if not EVENTS_PATH.exists():
            return []
        lines = EVENTS_PATH.read_text().splitlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
    except Exception:
        return []
