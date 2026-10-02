"""The inline tool-test target — a tiny vulnerable app the tool suites
boot themselves (http_replay, inject_probe, web_session, wave4_gates).

The old suites booted citadel; the lab is northbridge now and these
tests exercise TOOL MECHANICS, not the lab — so they carry their own
deterministic target with exactly the contracts they assert:
  /login (form auth, X-Session token), /api/docs/<id> (role 403 vs 200),
  /api/items (sqli 500 fingerprint), /api/register (mass assignment),
  /api/settings, /api/v2 + /api/v2/health (versioned surface),
  /search?fmt=json|html (reflected output), /download, /modals/<name>,
  /health. Zero deps on any lab.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

APP = r"""
import json, sqlite3, time, hmac, hashlib
from flask import Flask, jsonify, request, Response

app = Flask(__name__)
DB = app.config.get("DB") or "/tmp/suijin_tool_target.db"
PORT = int(__import__("os").environ.get("TOOL_TARGET_PORT", "0"))

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

@app.get("/health")
def health():
    return jsonify(ok=True, service="tool-target")


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *" + chr(10) + "Disallow: /private" + chr(10), mimetype="text/plain")

@app.post("/login")
def login():
    d = request.form if request.form else request.get_json(silent=True) or {}
    u, p = d.get("u") or d.get("username"), d.get("p") or d.get("password")
    if u == "alice" and p == "alice123":
        return jsonify(token="sess-alice-9f1")
    if u == "ceo" and p == "Zx!9topSecret":
        return jsonify(token="sess-ceo-a77")
    if u == "probe_admin" and p == "pw1":
        return jsonify(token="sess-probe_admin-t")  # registered via /api/register
    if CUSTOM_MSGS.get(f"sess-{u}-t"):
        return jsonify(token=f"sess-{u}-t")
    return jsonify(error="bad credentials"), 401

@app.get("/api/docs/<doc>")
def docs(doc):
    tok = request.headers.get("X-Session", "")
    if not tok:
        return jsonify(error="auth required"), 401
    if doc == "d-8b2e40d1" and not tok.startswith("sess-ceo"):
        return jsonify(error="forbidden"), 403  # classified
    body = "lorem"
    if doc == "d-8b2e40d1":
        body = "lorem FLAG{citadel_idor_docs}"  # the tool suite's known flag
    return jsonify(id=doc, title=f"doc {doc}", body=body)

@app.get("/api/items")
def items():
    cat = request.args.get("category", "")
    raw_q = request.query_string.decode("latin-1", errors="replace")
    # the citadel-contract WAF: boolean-injection shapes with a plain
    # space separator get the fake-404; %09 (tab) slips the separator
    # class and the sink still fires — the codec test's whole point
    if " OR " in cat.upper() and "%09" not in raw_q and "\t" not in cat:
        return jsonify(error="not found"), 404
    c = conn()
    try:
        if "'" in cat:  # the raw-concat sink the fingerprint keys on
            rows = c.execute(f"SELECT * FROM items WHERE category = '{cat}'").fetchall()
        else:
            rows = c.execute("SELECT * FROM items WHERE category = ?", (cat,)).fetchall()
    except sqlite3.OperationalError as e:
        return jsonify(error=f"sqlite: {e}"), 500
    if not rows and "OR" in cat.upper():
        # boolean TRUE via the OR — returns the full row set (200)
        rows = c.execute("SELECT * FROM items").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/items")
def items_search():
    # the sqli sink the fingerprint keys on
    q = str((request.get_json(silent=True) or {}).get("q", ""))
    c = conn()
    try:
        rows = c.execute(f"SELECT * FROM items WHERE note LIKE '%{q}%'").fetchall()
        return jsonify(items=[dict(r) for r in rows])
    except sqlite3.OperationalError as e:
        return jsonify(error=f"sqlite: {e}"), 500

CUSTOM_MSGS = {}


@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    role = d.get("role", "user")
    tok = f"sess-{d.get('username', 'x')}-t"
    CUSTOM_MSGS[tok] = d.get("custom_message", "")
    return jsonify(username=d.get("username"), role=role, token=tok)


@app.get("/message")
def message():
    # the SSTI reflect sink: a stored custom_message rendered raw
    tok = request.headers.get("X-Session", "")
    tpl = CUSTOM_MSGS.get(tok, "")
    if "{{" in tpl and "}}" in tpl:
        import re as _re

        expr = _re.findall(r"{{(.+?)}}", tpl)
        try:
            val = str(eval(expr[0])) if expr else tpl  # the sink
            return Response(f"<p>{val}</p>", mimetype="text/html")
        except Exception:
            return Response("<p>err</p>", mimetype="text/html")
    return Response(f"<p>{tpl}</p>", mimetype="text/html")

@app.route("/api/settings", methods=["GET", "POST", "PUT"])
def settings():
    if request.method == "PUT" or (request.data and request.method == "POST"):
        d = request.get_json(silent=True) or {}
        msg = str(d.get("custom_message", ""))
        tok = request.headers.get("X-Session", "")
        CUSTOM_MSGS[tok] = msg
        import re as _re

        if "{{" in msg and "}}" in msg:
            expr = _re.findall(r"{{(.+?)}}", msg)
            try:
                val = str(eval(expr[0])) if expr else msg  # the SSTI sink
                return jsonify(saved=True, rendered=f"{val}")
            except Exception:
                return jsonify(saved=True, rendered="err")
        return jsonify(saved=True, rendered=msg)
    return jsonify(two_factor=False, timezone="UTC", notify="email")

@app.get("/api/v2")
@app.get("/api/v2/health")
def v2():
    return jsonify(ok=True, v=2)


@app.get("/api/v2/executive")
def v2_executive():
    tok = request.headers.get("X-Session", "")
    if not tok or not tok.startswith("sess-ceo"):
        return jsonify(error="role required"), 403  # exists, role-gated
    return jsonify(board_pack=True)

@app.get("/search")
def search():
    q = request.args.get("q", "")
    fmt = request.args.get("fmt", "json")
    if fmt == "html":
        return Response(f"<p>results for {q}</p>", mimetype="text/html")
    return jsonify(hits=[], note="query accepted")  # JSON api: NO echo (the non-reflecting contract)

@app.get("/download")
def download():
    # citadel's contract: naive ../ blocked, ....// single-strip reads a file
    f = request.args.get("file", "report.pdf")
    if f.startswith("....//"):
        return Response("root:x:0:0:root:/root:/bin/sh\nFLAG{citadel_file_read}", mimetype="text/plain")
    if "../" in f and not f.startswith("....//"):
        return jsonify(error="blocked"), 400
    return jsonify(file=f, size=1024)

@app.get("/modals/<name>")
def modal(name):
    return Response(f"<div id='m-{name}'>modal {name}</div>", mimetype="text/html")

def seed():
    c = conn()
    c.execute("CREATE TABLE IF NOT EXISTS items (id TEXT, category TEXT, note TEXT)")
    c.execute("DELETE FROM items")
    for i in range(6):
        c.execute("INSERT INTO items VALUES (?,?,?)", (f"it-{i}", "hardware" if i % 2 else "software", f"note {i}"))
    c.commit()

seed()
app.run(host="127.0.0.1", port=PORT, threaded=True)
"""


def boot(port: int, db: str):
    """Spawn the target; returns (proc, base_url). Raises on boot failure."""
    import urllib.request

    proc = subprocess.Popen(
        [sys.executable, "-c", APP],
        env={**os.environ, "TOOL_TARGET_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if r.status == 200:
                    return proc, f"http://127.0.0.1:{port}"
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("tool target did not boot")


if __name__ == "__main__":
    p, base = boot(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "/tmp/tt.db")
    print(base)
