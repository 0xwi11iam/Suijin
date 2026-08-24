"""The Hill CTF — a heavily guarded compound. Public port 5910 (internal 5911).

Red's mission: climb four guarded perimeters and capture the rotating
token from the vault. ~10 chained flaws; no single shortcut. Stages:

  1 PERIMETER  misdirection: decoy admin panels (.git bait, /admin), a
               misleading robots.txt/security.txt; the real entry
               (/hill/login) is unlisted. A DECOY token validates false.
  2 FOOTHOLD  JWT auth: weak HS256 secret AND alg:none acceptance (two
               paths); IDOR /hill/api/docs/{id} leaks other users' docs
               (key fragment A + canary credentials live there).
  3 PIVOT     webhook SSRF -> internal metadata (port 5911, 127.0.0.1)
               -> temp creds -> internal API (key fragment B + vault
               route). Canary creds at metadata = critical trip.
  4 VAULT     the blob on the internal vault route; the decryptor
               interpolates fragments into a shell command — command
               injection IS the extraction. Token rotates every 15min.

Blue's surface: typed events (hill_events.jsonl), standard traffic JSONL
(blue_defend_traffic convention), and hill_defense.json levers (login
rate limit, SSRF blocklist, decoy sensitivity, force-rotate) — all
readable/writable from blue's shell access.
"""

import json
import os
import sys
import threading
import time
from urllib.parse import urlparse
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling imports

import auth
import data_seed
import defenses
import events
import vault
from flask import Flask, Response, jsonify, request

app = Flask("hill_public")
app.config["JSON_SORT_KEYS"] = False

PUBLIC_PORT = 5910
INTERNAL_PORT = 5911

# standard blue traffic log convention (the feed tails this)
TRAFFIC_LOG = os.environ.get("HILL_TRAFFIC_LOG", "/tmp/blue_defend_traffic.jsonl")


@app.before_request
def log_request():
    try:
        rec = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": request.method,
            "path": request.path,
            "query": request.args.to_dict(),
            "body": request.get_data(as_text=True)[:1000],
            "ip": request.remote_addr,
            "user_agent": request.headers.get("User-Agent", ""),
            "headers": {
                k: v
                for k, v in request.headers.items()
                if k.lower()
                in ("content-type", "cookie", "authorization", "x-hill-token", "x-admin", "origin", "refer")
            },
        }
        with open(TRAFFIC_LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


# ── STAGE 1: perimeter ─────────────────────────────────────────────────


@app.route("/")
def index():
    return jsonify(
        {
            "service": "The Hill",
            "motto": "the hill is watched",
            "endpoints": ["/", "/robots.txt", "/.well-known/security.txt"],
        }
    )


@app.route("/robots.txt")
def robots():
    return Response(data_seed.ROBOTS_TXT, mimetype="text/plain")


@app.route("/.well-known/security.txt")
def security_txt():
    return Response(data_seed.SECURITY_TXT, mimetype="text/plain")


@app.route("/admin", methods=["GET", "POST"])
def decoy_admin():
    events.emit("decoy_hit", path="/admin", ip=request.remote_addr)
    d = data_seed.DECOY_ENDPOINTS["/admin"]
    if request.method == "POST":
        # always-failing login on the bait panel
        return jsonify({**d["body"], "error": "invalid credentials"}), 401
    return jsonify(d["body"])


@app.route("/.git/config")
def git_bait():
    events.emit("decoy_hit", path="/.git/config", ip=request.remote_addr)
    return Response(data_seed.DECOY_ENDPOINTS["/.git/config"]["body"], mimetype="text/plain")


@app.route("/manager")
def manager_ghost():
    # security.txt points here; it does not exist (404 is the tell)
    return jsonify({"error": "not found"}), 404


# ── STAGE 2: foothold (auth + IDOR) ────────────────────────────────────


@app.route("/hill/login", methods=["POST"])
def hill_login():
    ip = request.remote_addr
    if not defenses.login_allowed(ip):
        return jsonify({"error": "rate limited"}), 429
    data = request.get_json(silent=True) or request.form
    u, p = data.get("user", ""), data.get("password", "")
    rec = data_seed.USERS.get(u)
    if not rec or rec["pw"] != __import__("hashlib").sha256(p.encode()).hexdigest():
        defenses.record_auth_fail(ip)
        events.emit("auth_fail", ip=ip, user=u[:20])
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"token": auth.issue(u, rec["role"]), "hint": "docs API at /hill/api/docs/{id}"})


@app.route("/hill/api/docs/<int:doc_id>", methods=["GET"])
def hill_docs(doc_id):
    # IDOR: token identifies the caller; ANY doc id is readable (the flaw)
    token = (request.headers.get("Authorization", "") or "").replace("Bearer ", "")
    claims, err = auth.decode(token) if token else (None, "no token")
    if not claims:
        return jsonify({"error": f"authentication required ({err})"}), 401
    doc = data_seed.DOCS.get(doc_id)
    if not doc:
        return jsonify({"error": "no such doc"}), 404
    if doc["owner"] != claims.get("sub"):
        events.emit("idor_access", doc=doc_id, from_user=claims.get("sub"), owner=doc["owner"])
    return jsonify({"id": doc_id, "title": doc["title"], "classification": doc["classification"], "body": doc["body"]})


@app.route("/hill/api/webhook", methods=["POST"])
def webhook():
    """STAGE 3: server-side fetch — the SSRF pivot to the internal port."""
    data = request.get_json(silent=True) or request.form
    target = str(data.get("url", ""))
    if not target:
        return jsonify({"error": "url required"}), 400
    events.emit("ssrf_attempt", target=target[:120], ip=request.remote_addr)
    if not defenses.ssrf_permitted(target):
        return jsonify({"error": "target refused by policy"}), 403
    parsed = urlparse(target)
    # only the INTERNAL port is fetchable (the design: pivot is through us)
    if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port != INTERNAL_PORT:
        return jsonify({"error": "webhook may only reach internal hill services"}), 400
    try:
        with urlopen(target, timeout=5) as r:
            return jsonify({"status": r.status, "body": r.read().decode(errors="replace")[:4000]})
    except Exception as e:  # noqa: BLE001 — lab surface
        return jsonify({"error": f"fetch failed: {e}"}), 502


# ── blue levers + token validator ──────────────────────────────────────


@app.route("/hill/token/validate", methods=["POST"])
def token_validate():
    data = request.get_json(silent=True) or request.form
    return jsonify(vault.validate(str(data.get("token", ""))))


@app.route("/hill/admin/state", methods=["GET"])
def admin_state():
    """Local-only admin surface (blue's shell): current levers + events."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "local only"}), 403
    if defenses.consume_force_rotate():
        vault.force_rotate("defense-file flag")
    return jsonify({"defense": defenses.admin_snapshot(), "recent_events": events.recent(20)})


@app.route("/hill/admin/rotate", methods=["POST"])
def admin_rotate():
    """Force-rotate lever (local only, per doc 103)."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "local only"}), 403
    out = vault.force_rotate("admin endpoint")
    return jsonify(out)


@app.route("/decoy-token")
def decoy_token():
    """The easy-path bait: always INVALID. Grabbing it is the loudest move."""
    events.emit("decoy_hit", path="/decoy-token", ip=request.remote_addr, detail="decoy token grabbed")
    return jsonify({"token": data_seed.DECOY_TOKEN, "note": "capture complete? validate it."})


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "the-hill"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", str(PUBLIC_PORT)))
    if os.environ.get("HILL_NO_INTERNAL") != "1":
        from internal import run_internal

        threading.Thread(target=run_internal, kwargs={"port": INTERNAL_PORT}, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False)
