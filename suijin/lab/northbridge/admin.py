"""admin — the internal panel (Flask, :6005).

Planted here:
  unauth diagnostics — /health?detail=1&target=<url> fetches attacker
      URLs WITH the internal token (the second-order SSRF that turns
      l4's webhook seed into m5's /__env read). A misconfig: the diag
      endpoint predates auth on the panel.
  CHAIN-1's crown — /admin/impersonate with a cookie signed by K.
  CHAIN-2's submit — /jobs (admin cookie) enqueues a worker job whose
      `template` field reaches a Jinja sink (the SSTI).
  CHAIN-3's payout — /vendor/payout (admin cookie, balance gate set by
      r1's race).

Cookie format (documented in the m2 git map + here): nbadmin=<user>|<ts>|
<hmac_sha256(K, user|ts)[:32]>. Anyone holding K forges a session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.request

from flask import Flask, jsonify, request

from suijin.lab.northbridge import K, PORT_ADMIN, ROOT, emit, internal_token

app = Flask(__name__)
QUEUE = os.path.join(ROOT, "queue")


def _cookie_ok() -> bool:
    c = request.cookies.get("nbadmin", "")
    parts = c.split("|")
    if len(parts) != 3:
        return False
    user, ts, mac = parts
    want = hmac.new(K.encode(), f"{user}|{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(want, mac)


def _core_balance(tenant: str) -> float:
    """Reads the tenant's credit balance from core's DB (same box)."""
    import sqlite3

    db = os.path.join(ROOT, "core.db")
    if not os.path.isfile(db):
        return 0.0
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT balance FROM tenants WHERE id=?", (tenant,)).fetchone()
    conn.close()
    return float(row["balance"]) if row else 0.0


@app.get("/health")
def health():
    """The unauth diagnostics feature (the CHAIN-1 hinge): detail mode
    fetches an arbitrary URL server-side WITH the internal token — how an
    SSRF becomes an /__env read. Internal services trust the token; the
    token never leaves this box by any other path."""
    detail = request.args.get("detail", "")
    if detail != "1":
        return jsonify(ok=True, service="admin")
    target = request.args.get("target", "")
    if not target.startswith("http://"):
        return jsonify(error="http:// target required"), 400
    emit("e_admin_diag", target[:120])
    try:
        req = urllib.request.Request(
            target,
            headers={"X-Internal-Token": internal_token(), "X-Internal": "1", "User-Agent": "nb-admin-diag/0.9"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            return jsonify(fetched=resp.status, body=resp.read(2000).decode(errors="replace"))
    except Exception as e:  # noqa: BLE001
        return jsonify(fetched=False, error=str(e)[:150]), 502


@app.get("/admin")
def panel():
    if not _cookie_ok():
        return jsonify(error="admin session required"), 401
    return jsonify(
        panel="northbridge internal",
        pages=["tenants", "jobs", "impersonate", "vendor payouts"],
        note="vendor payouts require tenant credit >= 75 (fraud control)",
    )


@app.post("/admin/impersonate")
def impersonate():
    if not _cookie_ok():
        emit("e_k_forge_cookie", "rejected: invalid cookie")
        return jsonify(error="admin session required"), 401
    d = request.get_json(silent=True) or {}
    tenant = str(d.get("tenant", ""))
    emit("e_c1_impersonate", tenant)
    return jsonify(
        impersonated=tenant,
        scope="full tenant control",
        flag="FLAG{northbridge_ssc_crown}",
    )


@app.post("/jobs")
def submit_job():
    """CHAIN-2's submit: `template` reaches the worker's Jinja sink. The
    spec (recoverable via l5's presign) documents the field."""
    if not _cookie_ok():
        return jsonify(error="admin session required"), 401
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()
    template = str(d.get("template", ""))
    if not name or not template:
        return jsonify(error="name and template required"), 400
    os.makedirs(QUEUE, exist_ok=True)
    jid = f"job-{int(time.time()*1000)}-{name}"
    with open(os.path.join(QUEUE, jid + ".json"), "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "id": jid,
                    "name": name,
                    "tenant": "northbridge",
                    "template": template,
                    "at": time.time(),
                    "via": "admin",
                }
            )
        )
    emit("e_ssti_submit", jid)
    return jsonify(enqueued=True, job=jid, poll=f"/job-out/{jid}")


@app.get("/job-out/<jid>")
def job_out(jid: str):
    p = os.path.join(ROOT, "exports", jid + ".out")
    if not os.path.isfile(p):
        return jsonify(error="not found"), 404
    return jsonify(job=jid, output=open(p, encoding="utf-8", errors="replace").read()[:4000])


@app.post("/vendor/payout")
def payout():
    """CHAIN-3's end: the fraud control (credit >= 75) is only passable
    when r1's race doubled the balance — and an admin cookie."""
    if not _cookie_ok():
        return jsonify(error="admin session required"), 401
    d = request.get_json(silent=True) or {}
    tenant = str(d.get("tenant", ""))
    bal = _core_balance(tenant)
    if bal < 75:
        return jsonify(error=f"insufficient credit ({bal:.0f}) — fraud control"), 403
    emit("e_payout", f"{tenant} at {bal}")
    emit("e_c3_toctou", tenant)
    return jsonify(paid=True, amount=bal, flag="FLAG{northbridge_toctou_crown}")


def run() -> None:
    app.run(host="127.0.0.1", port=PORT_ADMIN, threaded=True)


if __name__ == "__main__":
    run()
