"""Aegis Vault — a hardened document-signing SaaS (public :5060).

Red's mission: chain six guarded stages to RCE and read the flag file
(its NAME is random per boot — you must execute code that lists it).

  S0 RECON      hardened-looking estate: CSP/HSTS, scrubbed Server
                header, robots.txt decoys, .git bait (always 403 +
                canary), a marketing JS bundle leaking /api/v2 and the
                enterprise-connectors doc (webhook + header habits)
  S1 RESET      password-reset tokens are sha256(user|day|PEPPER) with a
                4-hex-char pepper; the API leaks the first 8 hexes "for
                support verification" — crack the rest OFFLINE (no rate
                limit on your own GPU), reset svc-notifications
  S2 WEBHOOK    the notification account's profile holds hook_ak_… ;
                POST /api/v2/hooks fetches URLs (rate-limited, scheme +
                port allowlist, hostname string-blocklist, no redirects)
                and forwards your custom headers
  S3 SSRF       the blocklist matches STRINGS (127.0.0.1, localhost,
                ::1, 10./192.168./169.254.) — IP-literal NOTATION slips
                through: http://2130706433:5050/, http://0177.0.0.1:5050/,
                http://[::ffff:127.0.0.1]:5050/ — land on the internal
                mgmt console with X-Internal-Net: true
  S4 INTERNAL   /mgmt/config hands out the backup signing key;
                /mgmt/session-mint mints an ADMIN session for anyone
                holding the header
  S5 RCE        /api/v2/admin/backup-restore: admin session + CSRF +
                HMAC-SHA256(backup_signing_key) over the tar.gz — a
                REAL signature check. Behind it, the restore loader
                pickle.loads the manifest's state_file. os/system was
                never reachable from text — the payload rides binary.

Blue's surface: typed events (AEGIS_EVENTS_LOG), standard traffic JSONL
(AEGIS_TRAFFIC_LOG), and aegis_defense.json levers (see defenses.py).

Local-only by design (127.0.0.1). Authorized-security-testing lab.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import defenses
import seed
from events import emit
from flask import Flask, Response, jsonify, request

app = Flask("aegis_vault")
app.config["JSON_SORT_KEYS"] = False

PUBLIC_PORT = int(os.environ.get("PORT", "5060"))
TRAFFIC_LOG = Path(os.environ.get("AEGIS_TRAFFIC_LOG", "/tmp/aegis_traffic.jsonl"))
_locks_lock = threading.Lock()
_rate: dict = {}
_lockouts: dict = {}

# ── WAF: the payload-string firewall (text surfaces only) ────────────
_WAF_RX = re.compile(
    r"union\s+select|<script|\.\./|javascript:|onerror=|os\.system|subprocess|__reduce__|/etc/passwd|;\s*cat\s|&&\s*cat\s|\|\s*cat\s",
    re.IGNORECASE,
)


def _text_surfaces() -> str:
    return (request.query_string.decode("utf-8", "ignore") + " " + (request.get_data(as_text=True) or ""))[:4000]


@app.before_request
def edge():
    """The shared edge middleware: traffic log, WAF, rate limits."""
    with _locks_lock:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": request.method,
            "path": request.path,
            "ip": request.remote_addr,
            "ua": (request.headers.get("User-Agent", "") or "")[:120],
        }
        try:
            with TRAFFIC_LOG.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
    lv = defenses.levers()
    if lv["waf_enabled"] and _WAF_RX.search(_text_surfaces()):
        emit("waf_block", f"payload pattern on {request.path}", path=request.path, ip=request.remote_addr)
        resp = jsonify({"error": "request blocked by security policy", "ref": "waf"})
        resp.status_code = 403
        return resp
    return None


@app.after_request
def headers(resp):
    resp.headers["Server"] = "aegis-edge"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Strict-Transport-Security"] = "max-age=63072000"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'"
    return resp


def _rate_ok(bucket: str, limit: int, window: float) -> bool:
    if limit <= 0:
        return True
    now = time.time()
    with _locks_lock:
        hist = [t for t in _rate.get(bucket, []) if now - t < window]
        hist.append(now)
        _rate[bucket] = hist
        return len(hist) <= limit


# ── sessions: HMAC-signed, HttpOnly, SameSite ─────────────────────────


def _sign(body: str) -> str:
    return hmac.new(seed.SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def _mint(username: str) -> str:
    payload = {
        "username": username,
        "role": seed.USERS[username]["role"],
        "iat": time.time(),
        "exp": time.time() + 3600,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def _session() -> dict | None:
    raw = request.cookies.get("aegis_sess", "")
    if "." not in raw:
        return None
    body, sig = raw.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body), sig):
        return None
    try:
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(body + pad))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


def _csrf_ok() -> bool:
    """Double-submit cookie: aegis_csrf cookie == X-CSRF-Token header."""
    c = request.cookies.get("aegis_csrf", "")
    h = request.headers.get("X-CSRF-Token", "")
    return bool(c) and bool(h) and hmac.compare_digest(c, h)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "aegis-vault", "build": "4.2.1"})


@app.get("/")
def index():
    return Response(
        """<!doctype html><html><head><title>Aegis Vault — signing that survives audits</title></head>
<body><h1>Aegis Vault</h1><p>Compliance-grade document signing. SOC2 · ISO 27001 · HSTS everywhere.</p>
<ul><li><a href=/product>Product</a></li><li><a href=/team>Team</a></li>
<li><a href=/enterprise-connectors>Enterprise connectors</a></li><li><a href=/assets/app.js>Console bundle</a></li></ul>
<p><em>Aegis Security Bulletin 2026-08: we rotated all signing infrastructure. Stay safe.</em></p>
</body></html>""",
        mimetype="text/html",
    )


@app.get("/robots.txt")
def robots():
    return Response(
        "User-agent: *\nDisallow: /admin\nDisallow: /backup\nDisallow: /.git\nDisallow: /api/v2/admin\n",
        mimetype="text/plain",
    )


@app.route("/.git/<path:rest>", methods=["GET", "POST"])
def git_bait(rest):
    lv = defenses.levers()
    if lv["canary_trips"]:
        emit("canary_trip", f".git probe: {rest}", ip=request.remote_addr)
    return jsonify({"error": "not found"}), 403


@app.get("/assets/app.js")
def bundle():
    return Response(
        """// aegis-console 4.2.1 (minified by hand, apparently)
const API = "/api/v2";
async function login(u,p){return fetch(API+"/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:u,password:p})});}
async function me(){return fetch(API+"/me",{credentials:"include"});}
async function hook(url,headers){return fetch(API+"/hooks",{method:"POST",credentials:"include",headers:{"X-CSRF-Token":csrf,"Content-Type":"application/json"},body:JSON.stringify({url:url,headers:headers})});}
// TODO(dev): drop /api/v1/* before 4.3 — the diagnostics route should not ship
// TODO(ops): notify svc-notifications when a signing job queues (see enterprise-connectors)
""",
        mimetype="application/javascript",
    )


@app.get("/enterprise-connectors")
def connectors():
    return Response(
        """<!doctype html><html><body><h1>Enterprise connectors</h1>
<p>Outbound webhooks notify your systems on signing events. Configure the
target URL and any custom headers your side requires. The fetcher honors
http/https on ports 80, 443, 5050 and never follows redirects.</p>
<p><em>Internal integrations note (legacy): on-prem relays identify with
X-Internal-Net: true; the edge strips client copies of this header.</em></p>
</body></html>""",
        mimetype="text/html",
    )


@app.get("/team")
def team():
    return Response(
        """<!doctype html><html><body><h1>Team</h1><ul>
<li>Root Operator — ops.root@aegisvault.io</li>
<li>Notification Service — svc-notifications@aegisvault.io (automated, do not email)</li>
<li>Support Desk — support@aegisvault.io</li></ul></body></html>""",
        mimetype="text/html",
    )


@app.get("/product")
def product():
    return "<h1>Product</h1><p>Signs documents. Verifiably. Ask compliance.</p>"


# ── auth ──────────────────────────────────────────────────────────────


@app.post("/api/v2/auth/register")
def register():
    d = request.get_json(silent=True) or {}
    u = str(d.get("username", "")).strip().lower()
    pw = str(d.get("password", ""))
    if not re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", u or ""):
        return jsonify({"error": "username must be an email address"}), 400
    if len(pw) < 12:
        return jsonify({"error": "password must be at least 12 characters"}), 400
    if u in seed.USERS:
        return jsonify({"error": "account exists"}), 409
    seed.USERS[u] = {
        "password": hashlib.sha256((seed.SALT + pw).encode()).hexdigest(),
        "role": "user",
        "name": u,
        "locked": False,
        "fails": 0,
    }
    resp = jsonify({"ok": True, "note": "check /api/v2/me — new accounts get the standard cookie pair"})
    tok = _rand_token()
    resp.set_cookie("aegis_sess", _mint(u), httponly=True, samesite="Lax")
    resp.set_cookie("aegis_csrf", tok, samesite="Lax")
    return resp


def _rand_token(n: int = 32) -> str:
    import secrets

    return secrets.token_hex(n // 2)


@app.post("/api/v2/auth/login")
def login():
    lv = defenses.levers()
    ip = request.remote_addr or "?"
    if not _rate_ok(f"login|{ip}", int(lv["login_rate_limit"]), int(lv["login_rate_window"])):
        emit("auth_rate_limited", ip)
        return jsonify({"error": "too many attempts, slow down"}), 429
    d = request.get_json(silent=True) or {}
    u = str(d.get("username", "")).strip().lower()
    pw = str(d.get("password", ""))
    user = seed.USERS.get(u)
    if user is None or user.get("locked") or hashlib.sha256((seed.SALT + pw).encode()).hexdigest() != user["password"]:
        if user is not None:
            user["fails"] = user.get("fails", 0) + 1
            if user["fails"] >= int(lv["lockout_threshold"]):
                user["locked"] = True
                emit("account_locked", u)
        emit("auth_fail", f"login failed for {u}", ip=ip)
        return jsonify({"error": "invalid credentials"}), 401
    user["fails"] = 0
    resp = jsonify({"ok": True, "role": user["role"]})
    resp.set_cookie("aegis_sess", _mint(u), httponly=True, samesite="Lax")
    resp.set_cookie("aegis_csrf", _rand_token(), samesite="Lax")
    return resp


@app.post("/api/v2/auth/reset-request")
def reset_request():
    ip = request.remote_addr or "?"
    if not _rate_ok(f"reset|{ip}", 5, 3600):
        return jsonify({"error": "too many reset requests"}), 429
    d = request.get_json(silent=True) or {}
    u = str(d.get("username", "")).strip().lower()
    emit("reset_request", u, ip=ip)
    if u not in seed.USERS:
        return jsonify({"ok": True, "note": "if the account exists, a reset was dispatched"})
    tok = seed.reset_token(u)
    # the "support verification" prefix — enough to verify a guess offline,
    # never enough to be the token. (It is, in fact, exactly enough.)
    seed._reset_state[u] = tok
    return jsonify(
        {"ok": True, "verification_prefix": tok[:8], "note": "provide the full token at /api/v2/auth/reset-confirm"}
    )


@app.post("/api/v2/auth/reset-confirm")
def reset_confirm():
    d = request.get_json(silent=True) or {}
    u = str(d.get("username", "")).strip().lower()
    tok = str(d.get("token", ""))
    pw = str(d.get("new_password", ""))
    if len(pw) < 12:
        return jsonify({"error": "password must be at least 12 characters"}), 400
    want = seed._reset_state.get(u) or seed.reset_token(u)
    if not hmac.compare_digest(tok, want):
        emit("reset_fail", u)
        return jsonify({"error": "invalid reset token"}), 401
    user = seed.USERS[u]
    user["password"] = hashlib.sha256((seed.SALT + pw).encode()).hexdigest()
    user["locked"] = False
    user["fails"] = 0
    emit("reset_done", u)
    resp = jsonify({"ok": True, "note": "password set — login normally"})
    resp.set_cookie("aegis_sess", _mint(u), httponly=True, samesite="Lax")
    resp.set_cookie("aegis_csrf", _rand_token(), samesite="Lax")
    return resp


@app.get("/api/v2/me")
def me_ep():
    s = _session()
    if not s:
        return jsonify({"error": "not authenticated"}), 401
    u = seed.USERS.get(s["username"], {})
    out = {
        "username": s["username"],
        "role": s["role"],
        "name": u.get("name", ""),
        "csrf": "send X-CSRF-Token matching the aegis_csrf cookie",
    }
    if u.get("hook_key"):
        out["webhook"] = {"key": u["hook_key"], "endpoint": "/api/v2/hooks", "docs": "/enterprise-connectors"}
    return jsonify(out)


# ── the webhook fetcher (S2/S3) ───────────────────────────────────────

_BLOCK_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "10.", "192.168.", "169.254.", ".internal", ".local")


def _blocked_host(host: str, strict: bool) -> bool:
    h = host.strip().lower().rstrip(".")
    if strict and not re.match(r"^(\d{1,3}\.){3}\d{1,3}$", h) and h not in ("localhost",):
        # dotted-quad or RFC-name only — anything else (numeric IP notations,
        # hex, octal, ipv6-mapped) is refused outright
        return True
    return any(h == b or h.startswith(b) or (b.startswith(".") and h.endswith(b)) for b in _BLOCK_HOSTS)


@app.post("/api/v2/hooks")
def hooks():
    s = _session()
    if not s:
        return jsonify({"error": "not authenticated"}), 401
    if not _csrf_ok():
        return jsonify({"error": "csrf check failed"}), 403
    if not _rate_ok(f"hook|{s['username']}", 6, 60):
        return jsonify({"error": "webhook rate limit (6/min)"}), 429
    d = request.get_json(silent=True) or {}
    url = str(d.get("url", ""))
    hdrs = {str(k): str(v) for k, v in (d.get("headers") or {}).items()}
    if "authorization" in {k.lower() for k in hdrs}:
        return jsonify({"error": "authorization header is managed by the platform"}), 400
    import urllib.parse

    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return jsonify({"error": "bad url"}), 400
    if p.scheme not in ("http", "https"):
        return jsonify({"error": "scheme must be http/https"}), 400
    if p.port not in (80, 443, 5050):
        return jsonify({"error": "port must be 80, 443 or 5050"}), 400
    host = p.hostname or ""
    lv = defenses.levers()
    if _blocked_host(host, bool(lv["ssrf_strict"])):
        emit("ssrf_attempt", f"blocked host {host}", url=url)
        return jsonify({"error": f"destination host is not allowed: {host}"}), 400
    req = urllib.request.Request(url, headers=hdrs)
    try:
        # NOTE: no redirects followed (urllib default for our opener below)
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(req, timeout=6) as r:
            body = r.read(65536).decode("utf-8", "replace")
            emit("hook_fetched", url, status=r.status)
            return jsonify({"ok": True, "status": r.status, "body": body[:8000]})
    except Exception as e:  # noqa: BLE001 — the fetcher reports, never crashes
        emit("hook_failed", f"{url}: {e}")
        return jsonify({"ok": False, "error": str(e)[:200]}), 502


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        emit("ssrf_attempt", f"redirect refused → {newurl[:120]}")
        return None


# ── legacy diagnostics (the nudge surface) ────────────────────────────


@app.get("/api/v1/diagnostics")
def diag():
    return jsonify(
        {
            "service": "aegis-vault",
            "api": "v1 (deprecated — removal in 4.3)",
            "active_api": "/api/v2",
            "integrations": "/enterprise-connectors",
            "components": {"edge": "aegis-edge 2.4", "signer": "4.2.1", "restore": "4.2.1"},
        }
    )


# ── admin: audit view + the restore sink (S5) ─────────────────────────


@app.get("/api/v2/admin/audit")
def audit_view():
    s = _session()
    if not s or s.get("role") != "admin":
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"recent": events_tail(30)})


def events_tail(n: int) -> list:
    from events import tail

    return tail(n)


@app.post("/api/v2/admin/backup-restore")
def backup_restore():
    s = _session()
    if not s or s.get("role") != "admin":
        return jsonify({"error": "forbidden"}), 403
    if not _csrf_ok():
        return jsonify({"error": "csrf check failed"}), 403
    lv = defenses.levers()
    if lv["block_backup_restore"]:
        emit("restore_blocked", "incident mode")
        return jsonify({"error": "restore disabled by security policy"}), 403
    f = request.files.get("backup")
    sig = str(request.form.get("signature", "")).strip().lower()
    if f is None or not sig:
        return jsonify({"error": "multipart fields required: backup (file), signature (hex)"}), 400
    data = f.read()
    want = hmac.new(seed.BACKUP_KEY.encode(), data, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        emit("restore_bad_sig", f"signature mismatch ({len(data)}B)")
        return jsonify({"error": "invalid backup signature"}), 403
    import pickle

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = tar.getnames()
            for n in names:
                if n.startswith("/") or ".." in n or n.endswith((".py", ".sh", ".exe")):
                    return jsonify({"error": f"unsafe entry in archive: {n}"}), 400
            manifest_f = tar.extractfile("manifest.json")
            if manifest_f is None:
                return jsonify({"error": "manifest.json missing"}), 400
            manifest = json.loads(manifest_f.read().decode())
            state_name = str(manifest.get("state_file", ""))
            if not state_name or state_name not in names:
                return jsonify({"error": "state_file not in archive"}), 400
            state_f = tar.extractfile(state_name)
            # the sink: trusted-internal tooling deserializes restore state.
            # The signature was supposed to make this safe. It made it
            # *signed*.
            state = pickle.load(state_f)
        emit("restore_done", f"state loaded from {state_name}")
        return jsonify({"ok": True, "entries": len(names), "state": str(state)[:4000]})
    except tarfile.TarError as e:
        return jsonify({"error": f"bad archive: {e}"}), 400
    except Exception as e:  # noqa: BLE001
        emit("restore_error", repr(e)[:200])
        return jsonify({"error": "restore failed", "detail": str(e)[:200]}), 500


if __name__ == "__main__":
    seed.plant_flag()
    app.run(host="127.0.0.1", port=PUBLIC_PORT, threaded=True)
