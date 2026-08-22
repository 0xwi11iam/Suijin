"""OAuth Lab — OAuth 2.0 / OIDC misconfiguration playground. Port 5902.

Flaws:
  1. Open redirect in authorization endpoint (redirect_uri not validated)
  2. Authorization code reuse (no single-use enforcement)
  3. Implicit grant with token in URL fragment (leaks via referrer)
  4. Weak state parameter (no CSRF protection)
  5. Client secret in frontend (public client claiming confidentiality)
  6. Scope escalation (granted scopes not checked at token exchange)
  7. ID token algorithm confusion (accepts alg:none)
  8. Userinfo endpoint without audience check
"""
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

from flask import Flask, jsonify, redirect, request

app = Flask(__name__)
app.config["SECRET_KEY"] = "oauth_lab_secret_5902"

CLIENTS = {
    "public-client": {"secret": None, "redirect_uris": ["http://localhost:3000/callback"], "confidential": False},
    "confidential-client": {"secret": "client_secret_5902", "redirect_uris": ["http://localhost:4000/callback"], "confidential": True},
    # VULNERABLE: claims confidential but secret is leaked in the frontend
    "leaked-client": {"secret": "leaked_secret_abc123", "redirect_uris": ["http://localhost:5000/callback"], "confidential": True},
}

USERS = {
    "alice": {"id": 1, "username": "alice", "email": "alice@example.com", "role": "user", "flag": "FLAG{oauth_user_alice_5902}"},
    "bob": {"id": 2, "username": "bob", "email": "bob@example.com", "role": "user", "flag": "FLAG{oauth_user_bob_5902}"},
    "admin": {"id": 3, "username": "admin", "email": "admin@example.com", "role": "admin", "flag": "FLAG{oauth_admin_privilege_5902}"},
}

codes_issued = {}  # code -> {user, scopes, client, used} (flaw 2: no single-use)
tokens_issued = {}  # token -> {user, scopes, client, audience}
state_store = {}  # state -> client (flaw 4: not enforced)


@app.route("/")
def index():
    return jsonify(
        {
            "issuer": "https://auth.lab.example",
            "grant_types": ["authorization_code", "implicit", "password", "client_credentials"],
            "endpoints": {
                "authorize": "/authorize",
                "token": "/token",
                "userinfo": "/userinfo",
                ".well-known/openid-configuration": "/.well-known/openid-configuration",
            },
            "registered_clients": list(CLIENTS.keys()),
            "note": "All clients use the leaked secret 'leaked_secret_abc123' — check the frontend JS",
        }
    )


@app.route("/.well-known/openid-configuration")
def oidc_config():
    return jsonify(
        {
            "issuer": "https://auth.lab.example",
            "authorization_endpoint": "/authorize",
            "token_endpoint": "/token",
            "userinfo_endpoint": "/userinfo",
            "jwks_uri": "/.well-known/jwks.json",
            "id_token_signing_alg_values_supported": ["RS256", "HS256", "none"],
            "scopes_supported": ["openid", "profile", "email", "admin"],
        }
    )


@app.route("/authorize")
def authorize():
    """Authorization endpoint — MULTIPLE flaws here."""
    client_id = request.args.get("client_id", "")
    redirect_uri = request.args.get("redirect_uri", "")
    response_type = request.args.get("response_type", "code")
    scope = request.args.get("scope", "openid profile")
    state = request.args.get("state", "")

    if client_id not in CLIENTS:
        return jsonify({"error": "invalid_client"}), 400

    # FLAW 1: redirect_uri not validated against registered list
    if not redirect_uri:
        redirect_uri = CLIENTS[client_id]["redirect_uris"][0]

    # FLAW 4: state is accepted but never validated on callback
    state_store[state] = client_id

    # simulate the user being already logged in as alice
    user = USERS["alice"]

    if response_type == "code":
        code = secrets.token_urlsafe(16)
        # FLAW 2: code not marked single-use
        codes_issued[code] = {"user": user["username"], "scopes": scope, "client": client_id, "used": False}
        params = {"code": code}
        if state:
            params["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return redirect(f"{redirect_uri}{sep}{urlencode(params)}")

    elif response_type == "token":
        # FLAW 3: implicit grant — token in URL fragment
        token = secrets.token_urlsafe(24)
        scopes_granted = scope  # FLAW 6: scopes not checked against granted
        tokens_issued[token] = {"user": user["username"], "scopes": scopes_granted, "client": client_id}
        fragment = f"access_token={token}&token_type=Bearer&scope={scope}"
        return redirect(f"{redirect_uri}#{fragment}")

    return jsonify({"error": "unsupported_response_type"}), 400


@app.route("/token", methods=["POST"])
def token():
    """Token endpoint — code reuse, scope escalation, secret issues."""
    grant_type = request.form.get("grant_type", "")

    if grant_type == "authorization_code":
        code = request.form.get("code", "")
        redirect_uri = request.form.get("redirect_uri", "")
        client_id = request.form.get("client_id", "")
        client_secret = request.form.get("client_secret", "")

        if code not in codes_issued:
            return jsonify({"error": "invalid_grant"}), 400

        code_data = codes_issued[code]

        # FLAW 2: code can be used multiple times
        # (we set used=True but never check it)
        code_data["used"] = True

        # FLAW 5: secret not verified (or the leaked one is accepted)
        if CLIENTS.get(client_id, {}).get("confidential") and client_secret != CLIENTS[client_id]["secret"]:
            return jsonify({"error": "invalid_client"}), 401

        user = USERS[code_data["user"]]
        access_token = secrets.token_urlsafe(24)

        # FLAW 6: scope escalation — grant everything requested, not just allowed
        scopes = code_data["scopes"]
        tokens_issued[access_token] = {"user": user["username"], "scopes": scopes, "client": client_id}
        return jsonify(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": scopes,
                "id_token": _make_id_token(user, client_id),
            }
        )

    elif grant_type == "password":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        # any password works (simulated IdP)
        if username in USERS:
            user = USERS[username]
            token = secrets.token_urlsafe(24)
            # FLAW 6: scope escalation — whatever scope is requested is granted, unchecked
            scopes = request.form.get("scope", "openid profile email")
            tokens_issued[token] = {"user": username, "scopes": scopes, "client": "resource_owner"}
            return jsonify({"access_token": token, "token_type": "Bearer", "scope": scopes})

    return jsonify({"error": "unsupported_grant_type"}), 400


@app.route("/userinfo")
def userinfo():
    """Userinfo — FLAW 8: no audience check (any token works)."""
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else request.args.get("access_token", "")
    if token in tokens_issued:
        data = tokens_issued[token]
        user = USERS[data["user"]]
        result = {"sub": user["id"], "username": user["username"], "email": user["email"], "role": user["role"]}
        # if admin scope was escalated, include the flag
        if "admin" in data["scopes"] or user["role"] == "admin":
            result["flag"] = user["flag"]
        return jsonify(result)
    return jsonify({"error": "invalid_token"}), 401


def _make_id_token(user, audience):
    """FLAW 7: creates a JWT with alg:none (unsigned)."""
    import base64

    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    header = {"alg": "none", "typ": "JWT"}  # FLAW 7: alg:none accepted
    payload = {"sub": user["id"], "iss": "https://auth.lab.example", "aud": audience, "exp": int(time.time()) + 3600, "role": user["role"]}
    return f"{b64(header)}.{b64(payload)}."


# ── Debug endpoints (info disclosure) ──────────────────────────────


@app.route("/debug/clients")
def debug_clients():
    """Leaks all client registrations including secrets."""
    return jsonify(CLIENTS)


@app.route("/debug/codes")
def debug_codes():
    """Leaks issued authorization codes."""
    return jsonify({k: v for k, v in codes_issued.items()})


@app.route("/debug/tokens")
def debug_tokens():
    """Leaks issued tokens (minus the actual token values)."""
    return jsonify({k[:8] + "...": v for k, v in tokens_issued.items()})


if __name__ == "__main__":
    import os

    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5902")), debug=False)
