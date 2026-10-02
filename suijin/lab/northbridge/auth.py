"""auth — login, JWT issuance, password reset, MFA (Flask, :6001).

Planted here:
  m5  /__env  — localhost-service-only: requires the per-boot internal
      token (never leaked), reached legitimately only through admin's
      diagnostics fetch. Full K + the internal job-spec path.
  l3  reset tokens are hash(email+minute) — a rate-limited puzzle, not
      a giveaway.
  l6  jwks exposes the "legacy" kid; ONE tenant's legacy key is derived
      from its name (weak) — forgeable after discovery.

Everything else does its job correctly (bcrypt-hashed passwords, locked
login rate limiting, MFA enrollment that actually enforces).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from functools import wraps

from flask import Flask, jsonify, request

from suijin.lab.northbridge import K, DEMO_PASS, DEMO_TENANT, DEMO_USER, PORT_AUTH, ROOT, emit, internal_token

app = Flask(__name__)
DB = os.path.join(ROOT, "auth.db")

LOGIN_BUCKETS: dict[str, list[float]] = {}
RESET_BUCKETS: dict[str, list[float]] = {}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _pw(pw: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), b"nb-salt", 40_000).hex()


def _jwt(user: str, tenant: str, role: str, kid: str = "primary") -> str:
    import base64

    def _b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = {"alg": "HS256", "kid": kid, "typ": "JWT"}
    payload = {"sub": user, "tenant": tenant, "role": role, "iat": int(time.time()), "exp": int(time.time()) + 3600}
    signing = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(payload).encode())
    key = _kid_key(kid, tenant)
    sig = hmac.new(key.encode(), signing.encode(), hashlib.sha256).hexdigest()
    return signing + "." + sig


def _kid_key(kid: str, tenant: str) -> str:
    """Primary kid signs with K. The legacy kid — a 2019 migration that
    never got rotated for one demo tenant — derives from the TENANT NAME:
    discoverable, weak, and exactly the kind of thing a real audit finds
    in a jwks endpoint."""
    if kid == "legacy" and tenant == DEMO_TENANT:
        return hashlib.sha256(tenant.encode()).hexdigest()
    return K


def _internal() -> bool:
    """m5's gate: the per-boot token. Edge strips X-Internal from
    inbound; only services (which read the token file) can present it."""
    return request.headers.get("X-Internal-Token", "") == internal_token()


def _rate(bucket: dict, key: str, limit: int, window: float = 60.0) -> bool:
    now = time.time()
    hits = [t for t in bucket.get(key, []) if now - t < window]
    if len(hits) >= limit:
        bucket[key] = hits
        return False
    hits.append(now)
    bucket[key] = hits
    return True


def seed() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = _db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
          email TEXT PRIMARY KEY, pw TEXT, tenant TEXT, role TEXT,
          mfa_secret TEXT, reset_hash TEXT, reset_at REAL, legacy INT DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?)",
        (DEMO_USER, _pw(DEMO_PASS), DEMO_TENANT, "tenant_admin", "JBSWY3DPEHPK3PXP", None, 0, 1),
    )
    conn.execute(
        "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?)",
        ("ops@northbridge.test", _pw("OpsInternal#2026"), "northbridge", "staff", None, None, 0, 0),
    )
    conn.commit()
    conn.close()


@app.post("/login")
def login():
    d = request.get_json(silent=True) or {}
    email, pw = str(d.get("email", "")), str(d.get("password", ""))
    if not email or not pw:
        return jsonify(error="email and password required"), 400
    if not _rate(LOGIN_BUCKETS, email, 8):
        return jsonify(error="too many attempts"), 429
    row = _db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row is None or not hmac.compare_digest(row["pw"], _pw(pw)):
        return jsonify(error="invalid credentials"), 401
    return jsonify(
        token=_jwt(row["email"], row["tenant"], row["role"]),
        mfa_required=bool(row["mfa_secret"]),
        tenant=row["tenant"],
        kid_hint="jwks at /auth/jwks" if row["tenant"] == DEMO_TENANT else None,
    )


@app.post("/mfa")
def mfa_verify():
    d = request.get_json(silent=True) or {}
    code = str(d.get("code", ""))
    email = str(d.get("email", ""))
    if not code.isdigit() or len(code) != 6:
        return jsonify(error="6-digit code required"), 400
    row = _db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row is None or not row["mfa_secret"]:
        return jsonify(error="no mfa enrolled"), 404
    import base64
    import struct

    key = base64.b32decode(row["mfa_secret"])
    step = int(time.time()) // 30
    ok = False
    for drift in (-1, 0, 1):  # honest TOTP with clock drift
        mac = hmac.new(key, struct.pack(">Q", step + drift), hashlib.sha1).digest()
        off = mac[-1] & 0x0F
        want = struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF
        if f"{want % 1_000_000:06d}" == code:
            ok = True
            break
    if not ok:
        return jsonify(error="mfa validation failed"), 401
    return jsonify(token=_jwt(row["email"], row["tenant"], row["role"]))


@app.get("/jwks")
def jwks():
    """Public by design (real jwks endpoints are). The legacy kid's
    existence is the l6 discovery."""
    return jsonify(
        keys=[
            {"kid": "primary", "alg": "HS256", "use": "sig"},
            {"kid": "legacy", "alg": "HS256", "use": "sig", "note": "pre-2020 tenants"},
        ]
    )


@app.post("/reset")
def reset_request():
    d = request.get_json(silent=True) or {}
    email = str(d.get("email", ""))
    if not _rate(RESET_BUCKETS, email, 5):
        return jsonify(error="too many reset requests"), 429
    row = _db().execute("SELECT email FROM users WHERE email=?", (email,)).fetchone()
    if row is None:
        return jsonify(ok=True)  # no user enumeration
    token = hashlib.sha256(f"{email}:{time.strftime('%Y%m%d%H%M')}".encode()).hexdigest()[:16]
    conn = _db()
    conn.execute("UPDATE users SET reset_hash=?, reset_at=? WHERE email=?", (token, time.time(), email))
    conn.commit()
    # a real system emails this; the LAB's delivery log is the stub
    os.makedirs(os.path.join(ROOT, "mail"), exist_ok=True)
    with open(os.path.join(ROOT, "mail", "outbox.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"to": email, "token": token}) + "\n")
    return jsonify(ok=True)


@app.post("/reset/confirm")
def reset_confirm():
    d = request.get_json(silent=True) or {}
    email, token, pw = str(d.get("email", "")), str(d.get("token", "")), str(d.get("password", ""))
    row = _db().execute("SELECT reset_hash, reset_at FROM users WHERE email=?", (email,)).fetchone()
    if row is None or not row["reset_hash"]:
        return jsonify(error="invalid token"), 401
    if time.time() - float(row["reset_at"] or 0) > 90:
        return jsonify(error="token expired"), 401
    if not hmac.compare_digest(row["reset_hash"], token):
        return jsonify(error="invalid token"), 401
    # l3's flag rides the SUCCESSFUL window prediction — proving the
    # caller derived the minute-hash rather than read the mailbox
    emit("l3_reset_window", email)
    conn = _db()
    conn.execute("UPDATE users SET pw=?, reset_hash=NULL WHERE email=?", (_pw(pw), email))
    conn.commit()
    return jsonify(ok=True, flag="FLAG{nb_tier_reset_window}")


@app.get("/__env")
def env_dump():
    """m5 — internal-only. Not leaked anywhere; the only legit caller is
    admin's diagnostics fetch. Full K + the job-spec pointer live here."""
    if not _internal():
        return jsonify(error="not found"), 404
    emit("e_m5_env", "auth /__env served over the internal path")
    return jsonify(
        JWT_SECRET=K,
        WEBHOOK_HMAC=K,
        ADMIN_COOKIE_KEY=K,
        internal_job_spec="s3://northbridge-internal/exports/internal-job-spec.json",
    )


@app.get("/health")
def health():
    return jsonify(ok=True, service="auth", port=PORT_AUTH)


def run() -> None:
    seed()
    app.run(host="127.0.0.1", port=PORT_AUTH, threaded=True)


if __name__ == "__main__":
    run()
