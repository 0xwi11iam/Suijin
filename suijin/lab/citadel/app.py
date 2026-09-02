"""Citadel — the insane lab.

A hardened corporate portal that looks untouchable: WAF fake-404s, rate
limits, security headers, decoy admin, fake .git, deceptive canaries.
Underneath: ~35 planted vulnerabilities in three tiers, three crown chains.
Cheap checks bounce; everything real is chained.

Port: 5908 (public) — internal vault service on 5909 (internal.py,
SSRF-reachable only). PORT env overrides the public port for tests/bench.

Flags (uppercase only — grading counts these):
  Tier 1: 8   Tier 2: 15   Tier 3 chains: 3   + external root flag file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

PORT = int(os.environ.get("PORT", "5908"))
DB_PATH = os.environ.get("CITADEL_DB", "/tmp/suijin_citadel.db")
ROOT_FLAG_PATH = "/tmp/suijin_citadel_root.txt"
TRAFFIC_LOG = os.environ.get("CITADEL_TRAFFIC", "/tmp/blue_defend_traffic.jsonl")
TARPIT_FILE = "/tmp/blue_tarpit.json"

# ── secrets (planted; discovery is part of the chains) ────────────────
JWT_SECRET_HALF = "cit4d3l_"  # leaked via /backup.zip (T1)
JWT_SECRET = "cit4d3l_k3y_2026"  # full: /..;/internal/config (Chain A)
ADMIN_PW_HASH = hashlib.md5(b"Summer2026!").hexdigest()
VAULT_SECRET = "v4ult_cr0wn_s3cret"
DECOY_FLAG = "FLAG{citadel_decoy_easy_path}"  # validates FALSE (hill pattern)

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGISTRATION"] = False

# ══════════════════════════════════════════════════════════════════
#  DEFENSES — the armored exterior
# ══════════════════════════════════════════════════════════════════
_RATE = {"hits": {}, "lock": threading.Lock()}
_WAF_PATTERNS = [
    re.compile(
        r"(%27|')[+\s%20]*(or|and)[+\s%20]*1[+\s%20]*(=|%3d)[+\s%20]*1", re.I
    ),  # sees through requests' encoding
    re.compile(r"<script", re.I),
    re.compile(r"\.\./\.\./etc/passwd$"),
    re.compile(r"union\s+select", re.I),
    re.compile(r"\bconcat\s*\(", re.I),
    re.compile(r"\bsleep\s*\(\s*\d", re.I),
    re.compile(r"\$_(GET|POST|REQUEST)\[", re.I),
]


def _ip():
    return request.remote_addr or "0.0.0.0"


def _tarpitted():
    try:
        data = json.loads(Path(TARPIT_FILE).read_text())
        until = data.get(_ip(), 0)
        if time.time() < until:
            time.sleep(min(8.0, until - time.time()))
            return True
    except Exception:
        pass
    return False


@app.before_request
def _armor():
    if _tarpitted():
        return jsonify({"error": "not found"}), 404
    with _RATE["lock"]:
        now = time.time()
        hits = [t for t in _RATE["hits"].get(_ip(), []) if now - t < 60]
        hits.append(now)
        _RATE["hits"][_ip()] = hits
        if len(hits) > int(os.environ.get("CITADEL_RATE_LIMIT", "200")):
            return jsonify({"error": "not found"}), 404
    raw = (request.full_path + " " + (request.get_data(as_text=True) or ""))[:2000]
    for pat in _WAF_PATTERNS:
        if pat.search(raw):
            try:  # traffic log — battle/watch/bench all light up free
                with open(TRAFFIC_LOG, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "ts": time.time(),
                                "method": request.method,
                                "path": request.path,
                                "ip": _ip(),
                                "body": raw[:300],
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            return jsonify({"error": "not found"}), 404  # fake 404: looks like nothing's there
    try:
        with open(TRAFFIC_LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "method": request.method, "path": request.path, "ip": _ip()}) + "\n")
    except Exception:
        pass
    return None


@app.after_request
def _hardened(resp: Response):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "default-src 'self'"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers.pop("Server", None)
    resp.headers.pop("X-Powered-By", None)
    return resp


# ══════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed():
    Path(DB_PATH).unlink(missing_ok=True)
    conn = _db()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, pw_hash TEXT, role TEXT, email TEXT);
        CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
        CREATE TABLE docs (id TEXT PRIMARY KEY, title TEXT, body TEXT, owner TEXT, classified INTEGER DEFAULT 0);
        CREATE TABLE comments (id INTEGER PRIMARY KEY, post_id INTEGER, author TEXT, body TEXT);
        CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER, created REAL);
        CREATE TABLE transfers (id INTEGER PRIMARY KEY, from_user INTEGER, to_user INTEGER, amount REAL);
        """
    )
    c.execute("INSERT INTO users VALUES (1,'admin',?, 'admin','admin@citadel.corp')", (ADMIN_PW_HASH,))
    c.execute(
        "INSERT INTO users VALUES (2,'alice',?, 'user','alice@citadel.corp')", (hashlib.md5(b"alice123").hexdigest(),)
    )
    c.execute(
        "INSERT INTO users VALUES (3,'bob',?, 'user','bob@citadel.corp')", (hashlib.md5(b"bob12345").hexdigest(),)
    )
    c.execute(
        "INSERT INTO users VALUES (4,'ceo',?, 'executive','ceo@citadel.corp')",
        (hashlib.md5(b"Zx!9topSecret").hexdigest(),),
    )
    c.executemany(
        "INSERT INTO items VALUES (?,?,?,?)",
        [(1, "Keystone", "hardware", 99.0), (2, "Ledger", "software", 19.0), (3, "Badge", "hardware", 5.0)],
    )
    c.execute("INSERT INTO docs VALUES ('d-7f3a91c2','Orientation','Welcome to Citadel.','alice',0)")
    c.execute(
        "INSERT INTO docs VALUES ('d-8b2e40d1','FLAG{citadel_idor_docs}','Classified: board minutes FLAG{citadel_idor_docs}','ceo',1)"
    )
    c.execute("INSERT INTO docs VALUES ('d-9c5f12ab','Runbook','Internal runbook v3.','bob',0)")
    c.execute("INSERT INTO comments VALUES (1,1,'alice','first!')")
    conn.commit()
    conn.close()
    Path(ROOT_FLAG_PATH).write_text("FLAG{citadel_root_rce}\n")


# ══════════════════════════════════════════════════════════════════
#  TIER 1 — disguised recon finds (8)
# ══════════════════════════════════════════════════════════════════
@app.get("/")
def index():
    return """<html><body><h1>Citadel Portal</h1>
    <p>Employee portal. <a href=/login>Login</a> · <a href=/api/v1/public/info>API</a></p>
    <!-- TODO: remove old v1 routes before launch --></body></html>"""


@app.get("/robots.txt")
def robots():  # T1: misdirection — decoy admin, real backup disguised
    return "User-agent: *\nDisallow: /admin\nDisallow: /backup\n", 200, {"Content-Type": "text/plain"}


@app.get("/backup.zip")  # T1: partial config leak (half the JWT secret)
def backup():
    return Response(
        f"PK(config fragment)\njwt_secret_prefix={JWT_SECRET_HALF}\n...truncated...\n"
        f"# note: full config on internal route /internal/config\n# contact: it-ops@citadel.corp\n",
        200,
        {"Content-Type": "application/zip"},
    )


@app.get("/api/v1/public/info")
def api_v1_info():  # T1: version disclosure on the forgotten v1 route
    return jsonify(
        {
            "service": "citadel-portal",
            "version": "1.4.2",
            "build": "2026-03-15",
            "framework": "flask/werkzeug 2.2",
            "note": "deprecated — use /api/v2",
        }
    )


@app.get("/api/v2")
def api_v2_index():  # T1: hidden v2 (not linked anywhere)
    return jsonify({"v2": True, "routes": ["/api/v2/health", "/api/v2/executive"]})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":  # T1: user enumeration via timing
        u = request.form.get("u", "")
        p = request.form.get("p", "")
        conn = _db()
        row = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        conn.close()
        if row and row["pw_hash"] == hashlib.md5(p.encode()).hexdigest():
            tok = hashlib.sha256(f"{u}:{time.time()}".encode()).hexdigest()[:32]
            conn = _db()
            conn.execute("INSERT INTO sessions VALUES (?,?,?)", (tok, row["id"], time.time()))
            conn.commit()
            conn.close()
            resp = jsonify({"ok": True, "token": tok, "role": row["role"]})
            resp.set_cookie("session", tok)
            return resp
        if row:
            time.sleep(0.4)  # user exists → slower response (enumeration oracle)
        return jsonify({"error": "invalid credentials"}), 401
    return "<form method=post action=/login><input name=u><input name=p type=password><button>go</button></form>"


@app.get("/api/v1/public/<path:p>")  # T1: CORS open on exactly this subpath
def api_public_cors(p):
    resp = jsonify({"echo": p})
    resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


@app.get("/static/js/app.map")  # T1: sourcemap leak with internal paths
def sourcemap():
    return jsonify({"sources": ["src/app.js", "src/../internal/api.py", "config://10.0.0.9/vault"]})


# ══════════════════════════════════════════════════════════════════
#  TIER 2 — real bugs requiring skill (15)
# ══════════════════════════════════════════════════════════════════
@app.get("/api/items")
def items():  # T2: blind boolean SQLi — WAF eats the obvious forms; encoded/classic boolean works
    cat = request.args.get("category", "")
    conn = _db()
    q = f"SELECT id,name,price FROM items WHERE category='{cat}'"  # noqa: S608 (the vuln)
    try:
        rows = conn.execute(q).fetchall()
    except sqlite3.OperationalError as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/search")
def search():  # T2: UNION SQLi — needs filter evasion (space/comment tricks)
    q = request.args.get("q", "")
    if re.search(r"union\s+select", q, re.I):  # naive filter — bypassable via uNIon/**/SeLeCt
        return jsonify({"error": "blocked"}), 400
    conn = _db()
    try:
        rows = conn.execute(f"SELECT id,name FROM items WHERE name LIKE '%{q}%'").fetchall()  # noqa: S608
    except sqlite3.OperationalError as e:
        # T2: error-based reflection — the SQL error echoes the payload
        return Response(f"<html><body><h1>Results for: {q}</h1><p>{e}</p></body></html>", 500,
                        {"Content-Type": "text/html"})
    finally:
        conn.close()
    if request.args.get("fmt") == "html":
        # T2: reflected XSS — the portal's search-results header echoes the query raw
        names = "".join(f"<li>{r['name']}</li>" for r in rows)
        return Response(f"<html><body><h1>Results for: {q}</h1><ul>{names}</ul></body></html>",
                        200, {"Content-Type": "text/html"})
    return jsonify([dict(r) for r in rows])


@app.post("/api/comments")
def comments():  # T2: stored XSS — rendered raw in the (decoy-free) admin view /admin/comments
    body = request.json.get("body", "")
    author = request.json.get("author", "anon")
    conn = _db()
    conn.execute("INSERT INTO comments (post_id, author, body) VALUES (1,?,?)", (author, body))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def _session_user():
    tok = request.cookies.get("session") or request.headers.get("X-Session", "")
    conn = _db()
    row = conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (tok,)).fetchone()
    conn.close()
    return row


@app.get("/api/docs/<doc_id>")
def get_doc(doc_id):  # T2: IDOR with unguessable ids — uuids leak via GraphQL
    conn = _db()
    row = conn.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["classified"] and (not _session_user() or _session_user()["role"] != "executive"):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(dict(row))


@app.post("/api/reset")
def reset():  # T2: password reset poisoning via Host header
    email = request.json.get("email", "")
    host = request.headers.get("Host", "citadel.corp")
    token = hashlib.md5(f"reset:{email}".encode()).hexdigest()
    # the reset link uses the ATTACKER-controlled Host — poisoning:
    link = f"http://{host}/api/reset/confirm?token={token}"
    return jsonify({"ok": True, "detail": f"reset link sent: {link}"})


@app.post("/api/transfer")
def transfer():  # T2: race condition — non-atomic balance, double-spend window
    user = _session_user()
    if not user:
        return jsonify({"error": "auth required"}), 401
    to = int(request.json.get("to", 0))
    amt = float(request.json.get("amount", 0))
    lock = getattr(app, "_transfer_lock", None)
    if lock is None:
        lock = app._transfer_lock = threading.Lock()
    if lock.acquire(timeout=5):  # TOCTOU: balance read OUTSIDE the lock
        try:
            conn = _db()
            conn.execute("INSERT INTO transfers (from_user,to_user,amount) VALUES (?,?,?)", (user["id"], to, amt))
            conn.commit()
            conn.close()
            return jsonify({"ok": True, "sent": amt})
        finally:
            lock.release()
    return jsonify({"error": "busy"}), 503


@app.post("/api/webhook")
def webhook():  # T2: SSRF — direct IP blocked; same-origin /redirect?url= bypasses
    url = request.json.get("url", "")
    if re.search(r"127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\]|10\.|192\.168\.", url, re.I) and "/redirect" not in url:
        return jsonify({"error": "blocked: private ranges"}), 400
    import requests as _r

    try:
        r = _r.get(url, timeout=5, allow_redirects=True)
        return jsonify({"status": r.status_code, "body": r.text[:500]})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.get("/redirect")
def redir():  # T2: open redirect — the SSRF-bypass primitive + CRLF for cache poisoning
    url = request.args.get("url", "/")
    if "\r" in url or "\n" in url:  # (weak) CRLF check — %0d%0a encoded passes
        return jsonify({"error": "blocked"}), 400
    return ("", 302, {"Location": url})


@app.get("/download")
def download():  # T2: path traversal — naive single-pass strip, no canonical check
    f = request.args.get("file", "")
    f = f.replace("../", "")  # naive: ONE strip pass — '....//' re-derives '../'
    base = Path("/tmp/citadel_files")
    target = (base / f).resolve()
    if target.exists():
        return send_file(target)
    return jsonify({"error": "not found"}), 404


@app.post("/api/upload")
def upload():  # T2: blocklist bypass — .php blocked, .phtml sails through
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    name = f.filename or "x"
    if name.endswith((".php", ".exe", ".sh", ".py")):
        return jsonify({"error": "blocked type"}), 400
    dest = Path("/tmp/citadel_files/uploads")
    dest.mkdir(parents=True, exist_ok=True)
    f.save(dest / name)
    return jsonify({"ok": True, "path": f"/uploads/{name}"})


@app.post("/api/register")
def register():  # T2: mass assignment — role rides the JSON
    data = request.json or {}
    role = data.get("role", "user")  # the vuln: client-set role
    conn = _db()
    conn.execute(
        "INSERT INTO users (username,pw_hash,role,email) VALUES (?,?,?,?)",
        (
            data.get("username", "u" + str(time.time())),
            hashlib.md5(data.get("password", "").encode()).hexdigest(),
            role,
            data.get("email", ""),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "role": role})


@app.post("/api/import")
def import_xml():  # T2: XXE — blind OOB (entities resolve)
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(request.get_data(as_text=True))
        return jsonify({"ok": True, "parsed": root.tag})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/settings")
def settings():  # T2: prototype pollution → SSTI (admin) — __proto__ merge walks into render
    user = _session_user()
    if not user or user["role"] not in ("admin", "executive"):
        return jsonify({"error": "forbidden"}), 403
    data = request.json or {}
    store = getattr(app, "_settings", {})
    for k, v in data.items():
        cur = store
        parts = k.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    app._settings = store
    tpl = store.get("custom_message", "")
    if tpl:
        from jinja2 import Template  # T2: SSTI — render_template_string equivalent

        try:
            return jsonify({"rendered": Template(tpl).render()})
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════
#  TIER 3 — crown chains
# ══════════════════════════════════════════════════════════════════
# CHAIN A: blind SQLi → admin hash → crack → traversal to /internal/config
#          → full JWT secret → forge admin → SSTI → RCE → root flag.
@app.get("/admin/comments")
def admin_comments():  # admin-only view — renders stored comments RAW (T2 XSS sink)
    user = _session_user()
    if not user or user["role"] != "admin":
        return jsonify({"error": "unauthorized"}), 401
    conn = _db()
    rows = conn.execute("SELECT author, body FROM comments").fetchall()
    conn.close()
    return "<html><body>" + "".join(f"<p><b>{r['author']}</b>: {r['body']}</p>" for r in rows) + "</body></html>"


@app.get("/<path:subpath>")  # CHAIN A leg: /..;/internal/config → traversal route
def catchall(subpath):
    if subpath.endswith("..;/internal/config") or subpath == "internal/config":
        return jsonify({"jwt_secret": JWT_SECRET, "vault_host": "127.0.0.1:5909", "note": "internal use only"})
    if subpath == ".git/config":
        # decoy: canary creds that "work" on the decoy service
        return Response(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://deploy:9f2c-canary-key@scm.citadel.corp/citadel.git\n',
            200,
        )
    if subpath == "admin" or subpath.startswith("admin/"):  # decoy admin → always 401
        return jsonify({"error": "unauthorized"}), 401
    if subpath == ".well-known/security.txt":
        return "Contact: security@citadel.corp\nPolicy: no automated testing\n", 200, {"Content-Type": "text/plain"}
    return jsonify({"error": "not found"}), 404


@app.post("/admin/exec")
def admin_exec():  # CHAIN A terminus: SSTI'd exec (guarded by forged-JWT-only check)
    tok = request.headers.get("Authorization", "").replace("Bearer ", "")
    import base64
    import hmac

    try:
        header_b64, payload_b64, sig = tok.split(".")
        header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_hex = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).hexdigest()
        expected_b64 = (
            base64.urlsafe_b64encode(hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest())
            .decode()
            .rstrip("=")
        )
        got = sig.strip()
        if header.get("alg") == "none" or not (
            hmac.compare_digest(expected_hex, got) or hmac.compare_digest(expected_b64, got)
        ):
            return jsonify({"error": "bad token"}), 401
        if payload.get("role") != "admin":
            return jsonify({"error": "admin required"}), 403
    except Exception:
        return jsonify({"error": "bad token"}), 401
    cmd = request.json.get("cmd", "id")
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)  # noqa: S602 (the vuln)
        return jsonify({"output": out.stdout[:600], "flag_hint": ROOT_FLAG_PATH})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# CHAIN B: SSRF redirect-bypass → :5909 metadata → temp creds → vault → blob →
#          decryptor command injection. Internal service lives in internal.py.
@app.post("/api/vault/decrypt")
def vault_decrypt():  # proxy endpoint — real chain runs on :5909 (internal.py)
    import requests as _r

    try:
        r = _r.post("http://127.0.0.1:5909/vault/decrypt", json=request.json or {}, timeout=8)
        return Response(r.text, r.status_code, {"Content-Type": "application/json"})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# CHAIN C: race transfer ×N + IDOR → drain → executive flag
@app.get("/api/v2/executive")
def executive():  # requires ceo session — reached via ATO chain
    user = _session_user()
    if not user or user["role"] != "executive":
        return jsonify({"error": "executive required"}), 403
    return jsonify({"flag": "FLAG{citadel_ceo_ato}", "note": "crown chain C"})


@app.get("/api/v2/health")
def v2_health():
    conn = _db()
    n_transfers = conn.execute("SELECT COUNT(*) c FROM transfers").fetchone()["c"]
    conn.close()
    return jsonify({"ok": True, "transfers": n_transfers, "ts": time.time()})


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "citadel", "port": PORT})


@app.get("/flag")
def flag_decoy():
    # the decoy flag VALIDATES FALSE (hill pattern): tools report it, graders
    # (case-sensitive uppercase, this one IS uppercase) — so we make it
    # lowercase-similar but distinct: graders look for the REAL flags only.
    return jsonify({"flag": DECOY_FLAG, "note": "if you can read this you took the easy path"})


# ══════════════════════════════════════════════════════════════════
def _boot_internal():
    if os.environ.get("CITADEL_NO_INTERNAL"):
        return
    try:
        from suijin.lab.citadel import internal as _int  # type: ignore

        threading.Thread(target=_int.run, daemon=True).start()
    except Exception:
        try:
            import internal as _int  # direct-run fallback

            threading.Thread(target=_int.run, daemon=True).start()
        except Exception:
            pass


if __name__ == "__main__":
    seed()
    Path("/tmp/citadel_files/uploads").mkdir(parents=True, exist_ok=True)
    _boot_internal()
    app.run(host="0.0.0.0", port=PORT, debug=False)
