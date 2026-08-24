"""Hill CTF internal services — metadata + internal API + vault. Port 5911.

Bound to 127.0.0.1 and reachable ONLY through the app's SSRF webhook
(the stage-3 pivot). Metadata issues role-scoped TEMP credentials; the
canary AWS keys 'work' here in the sense that using them trips the
loudest event in the system. The internal API hands out key fragment B
and the vault route.
"""

from __future__ import annotations

import secrets
import time

from data_seed import CANARY_VALUES
from events import emit

# temp creds issued by metadata: token -> {role, expires}
_temp_creds: dict[str, dict] = {}
TEMP_TTL = 20 * 60  # 20 minutes (per doc 104)


def _internal_app():
    from flask import Flask, jsonify, request

    app = Flask("hill_internal")

    @app.route("/metadata", methods=["GET"])
    def metadata():
        """Role-scoped temp credentials. Presenting a CANARY value is the
        critical trip (the tempting shortcut from the legacy doc)."""
        for cv in CANARY_VALUES:
            if cv in (request.query_string.decode() + str(request.headers)):
                emit("canary_metadata", detail="canary credential at metadata endpoint")
                # deceive: hand back a plausible-looking but useless pair
                return jsonify(
                    {
                        "Code": "Success",
                        "AccessKeyId": "ASIAHILLDECOY" + secrets.token_hex(4).upper(),
                        "SecretAccessKey": secrets.token_hex(16),
                        "Token": "canary-session-" + secrets.token_hex(8),
                        "Expiration": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
                    }
                )
        role = request.args.get("role", "surveyor")
        token = "hill-temp-" + secrets.token_hex(12)
        _temp_creds[token] = {"role": role, "expires": time.time() + TEMP_TTL}
        emit("metadata_access", role=role, granted=True)
        return jsonify(
            {
                "Code": "Success",
                "TempToken": token,
                "Role": role,
                "Expiration": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + TEMP_TTL)),
                "Note": "use TempToken as X-Hill-Token against the internal API",
            }
        )

    @app.route("/api/internal", methods=["GET"])
    def internal_api():
        """Fragment B + the vault route — requires a live temp token."""
        tok = request.headers.get("X-Hill-Token", "")
        rec = _temp_creds.get(tok)
        if not rec or rec["expires"] < time.time():
            emit("internal_api", ok=False, detail="missing/expired temp token")
            return jsonify({"error": "valid X-Hill-Token required (metadata grants one)"}), 401
        emit("internal_api", ok=True, role=rec["role"])
        return jsonify(
            {
                "service": "hill-internal-api",
                "key_fragment_b": "HILLKEY-PARTB-2d8e41",
                "vault_route": "/vault/blob (on this port)",
                "decryptor": "/vault/decrypt on this port — needs both fragments",
            }
        )

    @app.route("/vault/blob", methods=["GET"])
    def vault_blob():
        from vault import encrypt_blob

        tok = request.headers.get("X-Hill-Token", "")
        rec = _temp_creds.get(tok)
        if not rec or rec["expires"] < time.time():
            return jsonify({"error": "metadata temp token required"}), 401
        emit("vault_access", ok=True, detail="blob read")
        return jsonify({"blob": encrypt_blob(), "hint": "decrypt via /vault/decrypt"})

    @app.route("/vault/decrypt", methods=["POST"])
    def vault_decrypt():
        from flask import request as _r

        tok = _r.headers.get("X-Hill-Token", "")
        rec = _temp_creds.get(tok)
        if not rec or rec["expires"] < time.time():
            return jsonify({"error": "metadata temp token required"}), 401
        from vault import decrypt

        ok, out = decrypt(_r.form.get("fa", ""), _r.form.get("fb", ""))
        return jsonify({"ok": ok, "output": out})

    @app.route("/health")
    def health():
        return jsonify({"service": "hill-internal", "ok": True})

    return app


def run_internal(port: int = 5911) -> None:
    _internal_app().run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    run_internal()
