"""Aegis Vault defenses — the levers blue can pull.

aegis_defense.json (lab dir, read per-request) — blue's shell access
edits it mid-fight to change behavior. Missing file/keys = the hardened
defaults below (levers exist but the red path is fully live).

  waf_enabled          the payload-string firewall on text bodies/queries
  login_rate_limit     auth failures per IP per window (0 = off)
  lockout_threshold    per-account consecutive fails before lockout
  ssrf_strict          reject NON-DOTTED-QUAD hostnames + IP-literal
                       numerics (decimal/octal/hex/ipv6-mapped) in the
                       webhook fetcher — kills the notation bypasses
  block_backup_restore the RCE sink refuses every restore (incident mode)
  canary_trips         decoys/.git probes fire canary events
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

DEFENSE_PATH = Path(os.environ.get("AEGIS_DEFENSE_JSON", str(Path(__file__).resolve().parent / "aegis_defense.json")))
_lock = threading.Lock()

DEFAULTS = {
    "waf_enabled": True,
    "login_rate_limit": 10,
    "login_rate_window": 60,
    "lockout_threshold": 5,
    "ssrf_strict": False,
    "block_backup_restore": False,
    "canary_trips": True,
}


def levers() -> dict:
    with _lock:
        out = dict(DEFAULTS)
        try:
            data = json.loads(DEFENSE_PATH.read_text())
            if isinstance(data, dict):
                out.update({k: v for k, v in data.items() if k in DEFAULTS})
        except Exception:  # noqa: BLE001 — missing/corrupt file = defaults
            pass
        return out


def set_lever(name: str, value) -> None:
    with _lock:
        cur = {}
        with contextlib.suppress(Exception):
            cur = json.loads(DEFENSE_PATH.read_text())
        cur[name] = value
        DEFENSE_PATH.write_text(json.dumps(cur, indent=1))
