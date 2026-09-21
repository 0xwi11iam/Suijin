"""Aegis Vault seed — per-boot secrets, users, and the flag file.

Everything sensitive is minted once per boot and SHARED between the
public app and the internal mgmt console via AEGIS_SEED_JSON (the boot
harness writes it; both processes load it — no per-process randomness,
or session mints would fail signature checks across processes):
  SESSION_SECRET   signs session cookies
  RESET_PEPPER     4 lowercase hex, derived from the date — crackable
                   OFFLINE from the leaked token prefix
  BACKUP_KEY       32-byte hex, the HMAC key guarding backup restores
  FLAG             planted as flag_aegis_<hex>.txt in AEGIS_STATE_DIR —
                   the name is random per boot, so RCE must LIST then read
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import string
import time
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
SEED_FILE = os.environ.get("AEGIS_SEED_JSON", "")
STATE_DIR = Path(os.environ.get("AEGIS_STATE_DIR", LAB_DIR))


def _rand(n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(random.choice(alphabet) for _ in range(n))


def _fresh() -> dict:
    # daily pepper: same value all day (offline brute stays stable)
    _day = time.strftime("%Y-%m-%d")
    pepper = hashlib.sha256(("aegis-pepper|" + _day).encode()).hexdigest()[:4]
    return {
        "SESSION_SECRET": _rand(48),
        "BACKUP_KEY": _rand(64, string.hexdigits.lower()[:16]),
        "RESET_PEPPER": pepper,
        "FLAG": "FLAG{aegis_" + _rand(24, string.ascii_lowercase + string.digits) + "}",
        "FLAG_NAME": "flag_aegis_" + _rand(6, string.hexdigits.lower()[:16]) + ".txt",
        "SALT": _rand(16),
        "randomized": False,
    }


if SEED_FILE and Path(SEED_FILE).is_file():
    _secret = json.loads(Path(SEED_FILE).read_text())
    _secret["randomized"] = True
else:
    _secret = _fresh()
    if SEED_FILE:
        with contextlib.suppress(OSError):
            Path(SEED_FILE).write_text(json.dumps(_secret))


SESSION_SECRET: str = _secret["SESSION_SECRET"]
BACKUP_KEY: str = _secret["BACKUP_KEY"]
RESET_PEPPER: str = _secret["RESET_PEPPER"]
FLAG: str = _secret["FLAG"]
FLAG_NAME: str = _secret["FLAG_NAME"]
SALT: str = _secret["SALT"]


def _pw(pw: str) -> str:
    return hashlib.sha256((SALT + pw).encode()).hexdigest()


# users: password hashes are salted sha256 (no plaintext at rest)
USERS = {
    "ops.root@aegisvault.io": {
        "password": _pw(_rand(20)),
        "role": "admin",
        "name": "Root Operator",
        "locked": False,
        "fails": 0,
    },
    "svc-notifications@aegisvault.io": {
        "password": _pw(_rand(20)),
        "role": "service",
        "name": "Notification Service",
        "locked": False,
        "fails": 0,
        "hook_key": "hook_ak_" + _rand(36),
    },
    "support@aegisvault.io": {
        "password": _pw(_rand(20)),
        "role": "staff",
        "name": "Support Desk",
        "locked": False,
        "fails": 0,
    },
}

_reset_state: dict = {}


def reset_token(username: str) -> str:
    """Predictable reset token: sha256(user|day|pepper). The pepper is 4
    hex chars — the reset response leaks the first 8 token hexes 'for
    support verification', so the remainder is offline-crackable."""
    day = time.strftime("%Y-%m-%d")
    return hashlib.sha256(f"{username}|{day}|{RESET_PEPPER}".encode()).hexdigest()


def plant_flag() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = STATE_DIR / FLAG_NAME
    p.write_text(FLAG + "\n")
    return p
