"""core-api — the product API (FastAPI, :6002).

Planted here:
  m4  permissive CORS on /v1/ping (a trap that is exactly what it looks
      like and worth nothing)
  l1  mass assignment: PUT /v1/profile accepts `role`
  l2  IDOR: external invoices (EXT-*) skip the tenant check on ONE path
  l4  webhook SSRF: registration accepts internal targets and the sync
      delivery returns status+snippet (the read primitive)
  l6  token verification routes through the jwks kids (the weak legacy
      kid for one tenant is accepted HERE)
  r1  promo signup race (check-then-act credit)
  r3  export dedupe race (two rapid identical jobs both process)

Everything else is boring and correct: parameterized SQL, tenant checks
on every normal path, input validation, honest 404s.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from suijin.lab.northbridge import (
    DEMO_TENANT,
    K,
    PORT_AUTH,
    PORT_CORE,
    ROOT,
    emit,
)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
DB = os.path.join(ROOT, "core.db")
QUEUE = os.path.join(ROOT, "queue")
CREDIT_LOCK_FREE = True  # r1: the check-then-act has no lock


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


# ── auth middleware (l6 verification lives here) ────────────────────────


def _verify_jwt(token: str):
    """The verifier's bug museum, by kid:
    - primary:    HS256 with K (the Tier-3 reused key)
    - legacy:     HS256 with sha256(tenant) for ONE tenant (l6)
    - rsa-2026:   SHOULD be RS256 — but accepts HS256 signed with the
                  PUBLIC PEM (l7, the classic confusion). The RS256 path
                  verifies properly; the HS256 fallback is the hole.
    """
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    except Exception:  # noqa: BLE001 — malformed tokens are just invalid
        return None
    kid = str(header.get("kid", "primary"))
    alg = str(header.get("alg", "HS256"))
    tenant = str(payload.get("tenant", ""))
    ok = False
    if kid == "legacy":
        if tenant == DEMO_TENANT:
            want = hmac.new(hashlib.sha256(tenant.encode()).hexdigest().encode(),
                            (parts[0] + "." + parts[1]).encode(), hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(want, parts[2])
    elif kid == "rsa-2026":
        if alg == "RS256":
            from suijin.lab.northbridge.keys import rs256_verify

            ok = rs256_verify((parts[0] + "." + parts[1]).encode(), parts[2])
        else:
            # THE CONFUSION (l7): the verifier falls back to HMAC and
            # keys it with... the public certificate
            from suijin.lab.northbridge.keys import PUBLIC_PEM

            want = hmac.new(PUBLIC_PEM.encode(), (parts[0] + "." + parts[1]).encode(),
                            hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(want, parts[2])
            if ok:
                emit("l7_alg_confusion", payload.get("sub", "?"))
                payload["_confused_alg"] = True
    else:
        want = hmac.new(K.encode(), (parts[0] + "." + parts[1]).encode(), hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(want, parts[2])
    if not ok or int(payload.get("exp", 0)) < time.time():
        return None
    if kid == "legacy":
        payload["_forged_legacy"] = True  # served by /v1/me as the l6 proof
    return payload


@app.middleware("http")
async def authz(request: Request, call_next):
    public = ("/v1/ping", "/v1/tenants", "/health")
    if request.url.path.startswith(public) or request.method == "OPTIONS":
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "auth required"}, status_code=401)
    payload = _verify_jwt(auth[7:])
    if payload is None:
        return JSONResponse({"error": "invalid token"}, status_code=401)
    request.state.claims = payload
    return await call_next(request)


@app.middleware("http")
async def cors_ping(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/v1/ping":  # m4: the trap
        resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ── the GraphQL-ish surface (v2 beta) ────────────────────────────────────


@app.post("/v1/graphql")
async def graphql(request: Request):
    """A minimal GraphQL-shaped API — the beta surface real products ship.
    Mechanics planted here:
    - introspection: __schema dumps the graph (recon, no flag)
    - cross-tenant read: tenant(id:) resolves ANY tenant's projects —
      the authz gap GraphQL resolvers famously carry
    - depth bomb: nesting past 5 levels 400s (a defense that works)
    """
    d = await request.json()
    q = str(d.get("query", ""))
    if len(q) > 4000:
        return JSONResponse({"errors": ["query too large"]}, status_code=400)
    depth, cur = 0, 0
    for ch in q:
        if ch == "{":
            cur += 1
            depth = max(depth, cur)
        elif ch == "}":
            cur = max(0, cur - 1)
    if q.count("(") > 6 or depth > 5:
        return JSONResponse({"errors": ["query complexity exceeded"]}, status_code=400)
    if "__schema" in q or "__type" in q:
        return {
            "data": {
                "__schema": {
                    "types": [
                        {"name": "Query", "fields": ["me", "tenant(id: ID!)", "project(id: ID!)", "invoice(id: ID!)"]},
                        {"name": "Tenant", "fields": ["id", "name", "projects", "balance"]},
                        {"name": "Project", "fields": ["id", "name", "owner"]},
                    ]
                }
            }
        }
    conn = _db()
    import re as _re

    m = _re.search("tenant\\s*\\(\\s*id:\\s*\\\"?([A-Za-z0-9-]+)\\\"?", q)
    if m:
        tid = m.group(1)
        row = conn.execute("SELECT id, name, balance FROM tenants WHERE id=?", (tid,)).fetchone()
        if row is None:
            return {"data": {"tenant": None}}
        emit("x_graphql_tenant", tid)
        out = {"data": {"tenant": {"id": row["id"], "name": row["name"], "balance": row["balance"]}}}
        # the authz gap: ANY authenticated caller reads ANY tenant — flag
        # on reading a tenant that is not your own
        if request.state.claims.get("tenant") != tid:
            out["data"]["tenant"]["flag"] = "FLAG{nb_graphql_cross_tenant}"
        return out
    m = _re.search("project\\s*\\(\\s*id:\\s*\\\"?([A-Za-z0-9-]+)\\\"?", q)
    if m:
        return {"data": {"project": {"id": m.group(1), "name": "legacy-import", "owner": "northbridge"}}}
    return {"data": {"me": {"sub": request.state.claims.get("sub"), "role": request.state.claims.get("role")}}}


# ── public surface ──────────────────────────────────────────────────────


@app.get("/v1/ping")
def ping():
    return {"ok": True, "service": "core-api", "version": "2.4.1"}


@app.get("/health")
def health():
    return {"ok": True, "service": "core-api", "port": PORT_CORE}


@app.post("/v1/tenants")
def create_tenant(payload: dict):
    """r1 — the signup promo: check-then-act on promo_used with a real
    gap between the read and the write. Sequential re-registers skip
    (the flag is set); two CONCURRENT registrations both credit — the
    balance lands at 100 and the payout gate (>=75) opens.

    Deliberately a SYNC handler: FastAPI runs sync defs on the
    threadpool, so the check→act gap is genuinely racy (an async def
    with a blocking sleep serializes on the event loop and the race
    can never fire)."""
    d = payload or {}
    name = str(d.get("name", ""))[:60]
    promo = str(d.get("promo", ""))
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    conn = _db()
    tid = "t-" + hashlib.sha256(name.encode()).hexdigest()[:10]
    conn.execute(
        "INSERT OR IGNORE INTO tenants VALUES (?,?,?,?,?)", (tid, name, 0.0, 0, time.time())
    )
    conn.commit()
    # THE RACE: read the flag … (real gap: the await-free handler still
    # yields the GIL between these statements under concurrent load)
    row = conn.execute("SELECT balance, promo_used FROM tenants WHERE id=?", (tid,)).fetchone()
    if promo == "LAUNCH2026" and row and not row["promo_used"]:
        time.sleep(0.08)  # the window — generous for determinism, honest about the shape
        conn.execute("UPDATE tenants SET balance = balance + 50 WHERE id=?", (tid,))
        conn.execute("UPDATE tenants SET promo_used = 1 WHERE id=?", (tid,))
        conn.commit()
        row = conn.execute("SELECT balance FROM tenants WHERE id=?", (tid,)).fetchone()
        if row["balance"] >= 75:
            emit("e_r1_credit", f"race balance {row['balance']} on {tid}")
            return {"tenant": tid, "balance": row["balance"], "flag": "FLAG{nb_tier_race_credit}"}
        return {"tenant": tid, "balance": row["balance"]}
    row = conn.execute("SELECT balance FROM tenants WHERE id=?", (tid,)).fetchone()
    return {"tenant": tid, "balance": row["balance"]}


# ── tenant-authenticated surface ────────────────────────────────────────


@app.get("/v1/me")
def me(request: Request):
    c = request.state.claims
    out = {"sub": c["sub"], "tenant": c["tenant"], "role": c["role"]}
    if request.headers.get("X-Auth-Kid") == "legacy" or c.get("_forged_legacy"):
        out["flag"] = "FLAG{nb_tier_weak_kid_jwt}"
    if c.get("_confused_alg"):
        out["flag"] = "FLAG{nb_alg_confusion}"  # a forged RS256-account token walked in
        out["forged_via"] = "alg-confusion (HS256 keyed with the public PEM)"
    return out


@app.put("/v1/profile")
async def profile(request: Request):
    """l1 — mass assignment: `role` rides the body straight into the
    profile record (the PUT mirrors the whole dict like real code did)."""
    d = await request.json()
    allowed = {"display_name", "timezone", "newsletter"}
    stored = {k: str(v)[:120] for k, v in d.items() if k in allowed or k == "role"}
    if "role" in stored:
        emit("l1_massassign", f"role={stored['role']}")
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO profiles VALUES (?,?,?)",
        (request.state.claims["sub"], json.dumps(stored), int(time.time())),
    )
    conn.commit()
    return {"saved": sorted(stored)}


@app.get("/v1/profile")
def get_profile(request: Request):
    row = _db().execute(
        "SELECT data FROM profiles WHERE email=?", (request.state.claims["sub"],)
    ).fetchone()
    return json.loads(row["data"]) if row else {}


@app.get("/v1/invoices/{inv}")
def invoice(inv: str, request: Request):
    """l2 — external invoices (EXT-*) were bolted on later and skip the
    tenant check. Internal ones (INT-) check properly."""
    conn = _db()
    if inv.startswith("EXT-"):
        row = conn.execute("SELECT * FROM invoices WHERE id=?", (inv,)).fetchone()
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        emit("l2_idor", inv)
        return {"id": row["id"], "tenant": row["tenant"], "amount": row["amount"], "flag": "FLAG{nb_tier_idor}"}
    row = conn.execute(
        "SELECT * FROM invoices WHERE id=? AND tenant=?",
        (inv, request.state.claims["tenant"]),
    ).fetchone()
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"id": row["id"], "tenant": row["tenant"], "amount": row["amount"]}


@app.post("/v1/webhooks")
async def register_webhook(request: Request):
    """l4 — the SSRF seed. Delivery is SYNC (a "connectivity test" feature)
    and returns the target's status + first 200 bytes. Internal targets
    are NOT blocked. Tenant-auth only."""
    d = await request.json()
    url = str(d.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "url required"}, status_code=400)
    conn = _db()
    conn.execute(
        "INSERT INTO webhooks VALUES (?,?,?,?)",
        (request.state.claims["tenant"], url, 0, int(time.time())),
    )
    conn.commit()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Northbridge-Hook/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            snippet = resp.read(200).decode(errors="replace")
            status = resp.status
            headers = dict(resp.headers)
    except Exception as e:  # noqa: BLE001 — delivery failures are data
        return {"delivered": False, "error": str(e)[:120]}
    if "127.0.0.1" in url or "localhost" in url:
        emit("e_l4_ssrf", url[:120])
        emit("l4_ssrf_seed", url[:120])
    # the HMAC header proves K signs deliveries (the Tier-3 reuse story)
    mac = hmac.new(K.encode(), b"ping", hashlib.sha256).hexdigest()
    return {"delivered": True, "status": status, "snippet": snippet, "x_signature": mac, "resp_headers": headers}


@app.post("/v1/exports")
async def create_export(request: Request):
    """r3 — dedupe is check-then-act on the queue dir: two rapid identical
    submissions both enqueue (the worker claims race's seed)."""
    d = await request.json()
    name = str(d.get("name", ""))[:60]
    dedupe = bool(d.get("dedupe", False))
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    os.makedirs(QUEUE, exist_ok=True)
    if dedupe:
        # r3's bug shape: dedupe checks the IN-FLIGHT queue only — once
        # the worker claims a job (moves it out), the same name enqueues
        # again and double-processes. A check-then-act against a window
        # that moves — the honest TOCTOU, winnable over HTTP.
        if any(name in f for f in os.listdir(QUEUE)):
            return {"enqueued": False, "reason": "duplicate"}
    jid = f"job-{int(time.time()*1000)}-{name}"
    with open(os.path.join(QUEUE, jid + ".json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": jid, "name": name, "tenant": request.state.claims["tenant"], "at": time.time()}))
    return {"enqueued": True, "job": jid}


@app.get("/v1/exports/{jid}")
def export_status(jid: str):
    out = os.path.join(ROOT, "exports", jid + ".out")
    if not os.path.isfile(out):
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"job": jid, "output": open(out, encoding="utf-8", errors="replace").read()[:2000]}


def seed() -> None:
    os.makedirs(ROOT, exist_ok=True)
    conn = _db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenants(
          id TEXT PRIMARY KEY, name TEXT, balance REAL, promo_used INT, created REAL
        );
        CREATE TABLE IF NOT EXISTS profiles(email TEXT PRIMARY KEY, data TEXT, at INT);
        CREATE TABLE IF NOT EXISTS invoices(
          id TEXT PRIMARY KEY, tenant TEXT, amount REAL
        );
        CREATE TABLE IF NOT EXISTS webhooks(
          tenant TEXT, url TEXT, failures INT, created INT
        );
        """
    )
    conn.execute("INSERT OR REPLACE INTO invoices VALUES (?,?,?)", ("INT-2026-0042", DEMO_TENANT, 240.0))
    conn.execute("INSERT OR REPLACE INTO invoices VALUES (?,?,?)", ("EXT-2026-0199", "northbridge", 19000.0))
    conn.commit()
    conn.close()


def run() -> None:
    import uvicorn

    seed()
    uvicorn.run(app, host="127.0.0.1", port=PORT_CORE, log_level="warning")


if __name__ == "__main__":
    run()
