"""Hill CTF vault — rotating token, fragments, command-injection decryptor.

Stage 4: the token blob on the internal vault route is encrypted; the
decryptor endpoint interpolates the two key fragments into a shell
command (the extraction IS the command-injection exploit). The token
rotates every 15 minutes; a validator tells red whether a capture is
still live; force-rotate is blue's kill lever.
"""

from __future__ import annotations

import secrets
import subprocess
import threading
import time

from events import emit

ROTATE_INTERVAL = 15 * 60  # 15 minutes
_lock = threading.Lock()
_state = {"token": "", "issued_at": 0.0, "generation": 0}


def _mint() -> None:
    _state["token"] = "FLAG{the_hill_" + secrets.token_hex(8) + "}"
    _state["issued_at"] = time.time()
    _state["generation"] += 1


def current_token() -> str:
    with _lock:
        if not _state["token"] or time.time() - _state["issued_at"] >= ROTATE_INTERVAL:
            _mint()
            emit("token_rotated", generation=_state["generation"], reason="scheduled")
        return _state["token"]


def force_rotate(reason: str = "manual") -> dict:
    with _lock:
        _mint()
        gen = _state["generation"]
    emit("token_rotated", generation=gen, reason="force:" + reason[:60])
    return {"generation": gen, "note": "previously captured tokens are now invalid"}


def validate(token: str) -> dict:
    cur = current_token()
    emit("token_validated", verdict=str(token == cur))
    return {"valid": token == cur, "generation": _state["generation"]}


def encrypt_blob() -> str:
    """The blob served on the internal vault route (fake cipher wrapper —
    the 'encryption' is resolved server-side by the decryptor)."""
    tok = current_token()
    return f"HILLVAULT1:{tok[len('FLAG{') : -1]}"  # deliberately naive


FRAGMENT_A_PREFIX = "HILLKEY-PARTA"
FRAGMENT_B_PREFIX = "HILLKEY-PARTB"


def decrypt(fragment_a: str, fragment_b: str):
    """FLAW: fragments interpolate into a shell echo — command injection is
    the intended extraction path (both fragments arrive from stage 2/3).

    Returns (ok, output). Injection payloads that command-substitute or
    chain commands escape the echo and leak the token."""
    if not (
        str(fragment_a or "").startswith(FRAGMENT_A_PREFIX) and str(fragment_b or "").startswith(FRAGMENT_B_PREFIX)
    ):
        emit("vault_decrypt", ok=False, detail="wrong fragments")
        return False, "fragments do not match vault key halves"
    emit("vault_decrypt", ok=True, detail="decryptor invoked")
    cmd = f"echo 'key={fragment_a}-{fragment_b}'"
    try:
        out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=5)
        blob = encrypt_blob()
        return True, out.stdout.strip() + "\nvault-blob: " + blob
    except Exception as e:  # noqa: BLE001 — lab surface, errors are data
        return False, f"decryptor error: {e}"
