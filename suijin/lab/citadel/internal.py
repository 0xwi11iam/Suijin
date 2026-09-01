"""Citadel internal service — :5909, SSRF-reachable only.

Chain B target: metadata with DECEPTIVE fake creds on the wrong path,
temp creds on the right one, a rotating-token vault, and a decryptor
whose command injection IS the extraction (hill pattern).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import subprocess
import threading
import time

from flask import Flask, jsonify, request

INTERNAL_PORT = int(os.environ.get("CITADEL_INTERNAL_PORT", "5909"))
VAULT_FLAG = "FLAG{citadel_vault_crown}"
VAULT_KEY = "citadel-vault-key-2026"
TOKEN_TTL = 15 * 60

app = Flask(__name__)

_state = {"token": "", "token_at": 0.0, "lock": threading.Lock()}


def _rotate():
    with _state["lock"]:
        _state["token"] = hashlib.sha256(f"{VAULT_KEY}:{time.time()}".encode()).hexdigest()[:32]
        _state["token_at"] = time.time()


def _current_token():
    with _state["lock"]:
        if time.time() - _state["token_at"] > TOKEN_TTL or not _state["token"]:
            _state["token"] = hashlib.sha256(f"{VAULT_KEY}:{time.time()}".encode()).hexdigest()[:32]
            _state["token_at"] = time.time()
        return _state["token"]


@app.before_request
def _internal_only():
    # internal: only loopback (the SSRF proxy runs in-process on 127.0.0.1)
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "not found"}), 404
    return None


@app.get("/metadata")  # DECEPTIVE: wrong path returns FAKE creds (hill canary pattern)
def metadata_fake():
    return jsonify(
        {
            "access_key": "AKIAFAKECANARY001",
            "secret_key": "c4n4ry-fake-creds-not-real-9f2c",
            "note": "use /metadata/v2/ for temporary credentials",
        }
    )


@app.get("/metadata/v2/")  # REAL temp creds (SSRF chain leg 2)
def metadata_real():
    creds = {
        "access_key": "ASIA-CITADEL-TEMP",
        "secret_key": hmac.new(VAULT_KEY.encode(), b"temp", hashlib.sha256).hexdigest()[:24],
        "token": hmac.new(VAULT_KEY.encode(), b"session", hashlib.sha256).hexdigest()[:24],
        "vault_api": "/vault/blob?token=<rotating>",
        "vault_token_url": "/vault/token",
    }
    return jsonify(creds)


@app.get("/vault/token")  # rotating token (chain leg 3)
def vault_token():
    return jsonify({"token": _current_token(), "ttl": TOKEN_TTL})


@app.get("/vault/blob")  # encrypted blob (chain leg 4)
def vault_blob():
    tok = request.args.get("token", "")
    if not hmac.compare_digest(tok, _current_token()):
        return jsonify({"error": "invalid or expired token"}), 401
    blob = {
        "format": "CITADELVAULT1",
        "ciphertext": "9f2c4d7b1e8a6305c4d2b7e9",
        "flag_wrapped": True,
        "decrypt_hint": 'POST /vault/decrypt {"ciphertext": ..., "key": ...}',
    }
    return jsonify(blob)


_BLOB_CIPHER = "9f2c4d7b1e8a6305c4d2b7e9"


@app.post("/vault/decrypt")  # command injection IS the extraction (chain terminus)
def vault_decrypt():
    data = request.json or {}
    cipher = str(data.get("ciphertext", ""))
    key = str(data.get("key", ""))
    if not cipher:
        return jsonify({"error": "ciphertext required"}), 400
    # "decryption" shells out — the key field is injectable
    try:
        out = subprocess.run(
            f"echo {cipher} | openssl enc -d -aes256 -k {key}",  # noqa: S602 (the vuln)
            shell=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
        rendered = out.stdout or out.stderr
        if cipher == _BLOB_CIPHER:  # the vault's own blob unwraps the crown
            rendered = (rendered or "") + f"\n{VAULT_FLAG}"
        return jsonify({"decrypted": rendered[:500]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/health")
def health():
    return jsonify({"ok": True, "internal": True})


def run():
    _rotate()
    app.run(host="127.0.0.1", port=INTERNAL_PORT, debug=False)


if __name__ == "__main__":
    run()
