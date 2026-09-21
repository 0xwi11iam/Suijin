"""Aegis Vault events — typed blue-team feed (standard lab convention).

Every security-relevant moment appends to /tmp/aegis_events.jsonl (env
AEGIS_EVENTS_LOG): auth failures, lockouts, reset requests, WAF blocks,
SSRF attempts, canary trips, internal-config reads, session mints,
backup restores, and RCE detonations. Blue's watchers tail this file.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
LOG = Path(os.environ.get("AEGIS_EVENTS_LOG", "/tmp/aegis_events.jsonl"))


def emit(etype: str, detail: str = "", **kw) -> None:
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": etype,
        "detail": detail[:300],
        **kw,
    }
    with _lock:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass


def tail(limit: int = 20) -> list[dict]:
    try:
        lines = LOG.read_text().splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:  # noqa: BLE001
        return []
