"""Aegis Vault internal management console — 127.0.0.1 only.

The pattern this stage teaches: an internal service that trusts its
NETWORK POSITION instead of an authenticated caller. Every route accepts
any request carrying `X-Internal-Net: true` (the header the edge proxy
allegedly injects). In a real deployment the proxy strips client-supplied
copies; here — as in far too many real estates — nobody checked.

  GET  /mgmt/config          the estate: backup signing key, hook tokens
  POST /mgmt/session-mint    mint a signed session for ANY user (the SSO
                             bridge the front app is supposed to call)

Runs on 127.0.0.1:8080 (MGMT_PORT) — never exposed, only reachable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed  # noqa: E402
from events import emit  # noqa: E402
from flask import Flask, jsonify, request  # noqa: E402

app = Flask("aegis_mgmt")
MGMT_PORT = int(os.environ.get("MGMT_PORT", "8080"))


@app.before_request
def internal_gate():
    # trust-the-network auth: the one header the edge "always sets".
    # red discovers it via the webhook docs (the integration guide the
    # marketing site ships for "enterprise connectors").
    if request.path == "/health":  # liveness only — says nothing else exists
        return None
    if request.headers.get("X-Internal-Net", "").lower() != "true":
        emit("mgmt_refused", "missing X-Internal-Net", path=request.path)
        return jsonify({"error": "not found"}), 404
    return None


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "aegis-mgmt"})


@app.get("/mgmt/config")
def config():
    emit("internal_config_read", "config dump served", ip=request.remote_addr)
    return jsonify(
        {
            "service": "aegis-mgmt",
            "build": "4.2.1-internal",
            "backup_signing_key": seed.BACKUP_KEY,
            "hooks": {
                "notifier": {
                    "account": "svc-notifications@aegisvault.io",
                    "key": seed.USERS["svc-notifications@aegisvault.io"]["hook_key"],
                    "allowlist_ports": [80, 443, 8080],
                }
            },
            "notes": "edge strips X-Internal-Net from client traffic — verify before 5.x",
        }
    )


@app.post("/mgmt/session-mint")
def session_mint():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    user = seed.USERS.get(username)
    if user is None:
        return jsonify({"error": "unknown principal"}), 400
    import base64
    import hashlib
    import hmac

    payload = {
        "username": username,
        "role": user["role"],
        "iat": __import__("time").time(),
        "exp": __import__("time").time() + 3600,
    }
    body = base64.urlsafe_b64encode(__import__("json").dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(seed.SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    emit("session_minted", f"internal mint for {username}", role=user["role"])
    return jsonify({"session": f"{body}.{sig}", "role": user["role"]})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=MGMT_PORT, threaded=True)
