"""Hill CTF defenses — the levers blue can actually pull TODAY.

hill_defense.json (lab dir) is read per-request; blue's shell access
(execute_terminal / the gated shell) edits it to change behavior:

  login_rate_limit   max auth failures per IP per window (0 = off)
  ssrf_blocklist     list of internal targets the webhook refuses
  decoy_sensitivity  "high" = decoy hits also tarpit via the standard file
  force_rotated      one-shot: next vault read mints a fresh token

The standard blue tarpit file convention is honored independently.
Missing file / keys = defaults (levers exist but off/neutral).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from events import emit

LAB_DIR = Path(__file__).resolve().parent
DEFENSE_PATH = Path(__file__).resolve().parent / "hill_defense.json"

DEFAULTS = {
    "login_rate_limit": 8,  # failures per window per IP
    "login_rate_window": 60,
    "ssrf_blocklist": [],  # e.g. ["127.0.0.1:5911/metadata"]
    "decoy_sensitivity": "high",
    "force_rotated": False,
}

_lock = threading.Lock()
_fail_counts: dict[str, list] = {}  # ip -> [timestamps]


def _load() -> dict:
    try:
        data = json.loads(DEFENSE_PATH.read_text())
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except Exception:
        return dict(DEFAULTS)


def login_allowed(ip: str) -> bool:
    """Rate lever: False when the IP exceeded auth failures in the window.
    Every rejection emits rate_limited (blue's velocity signal)."""
    cfg = _load()
    limit = int(cfg.get("login_rate_limit") or 0)
    if limit <= 0:
        return True
    window = int(cfg.get("login_rate_window") or 60)
    now = time.time()
    with _lock:
        fails = [t for t in _fail_counts.get(ip, []) if now - t < window]
        if len(fails) >= limit:
            emit("rate_limited", ip=ip, detail=f"{len(fails)} auth failures/{window}s")
            return False
    return True


def record_auth_fail(ip: str) -> None:
    with _lock:
        _fail_counts.setdefault(ip, []).append(time.time())
        fails = _fail_counts[ip]
        if len(fails) >= 5:
            emit("auth_fail_velocity", ip=ip, count=len(fails))


def ssrf_permitted(target: str) -> bool:
    """Blocklist lever: webhook fetches to listed targets are refused.
    The refusal still emits ssrf_blocked (blue sees the attempt)."""
    blocked = [str(b) for b in _load().get("ssrf_blocklist") or []]
    hit = any(b and b in target for b in blocked)
    if hit:
        emit("ssrf_blocked", target=target[:80])
        return False
    return True


def decoy_is_loud() -> bool:
    return str(_load().get("decoy_sensitivity", "high")).lower() == "high"


def consume_force_rotate() -> bool:
    """One-shot force-rotate flag (set by blue via shell)."""
    try:
        with _lock:
            if DEFENSE_PATH.exists() and _load().get("force_rotated"):
                data = json.loads(DEFENSE_PATH.read_text())
                data["force_rotated"] = False
                DEFENSE_PATH.write_text(json.dumps(data, indent=2))
                return True
    except Exception:
        pass
    return False


def admin_snapshot() -> dict:
    """What /hill/admin surfaces to blue's shell: current levers."""
    snap = _load()
    snap["note"] = "edit hill_defense.json next to app.py to change levers"
    return snap
