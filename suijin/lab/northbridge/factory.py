"""The service factory — stamps real services from the catalog.

One generic Flask app per catalog entry: the handler library covers the
endpoint kinds (list/get/create/update/search/static/metrics/health…)
and the vuln library wraps handlers with the planted pattern. Content
comes from data.py (deterministic). This is how the lab gets BIG
without hand-writing fifteen codebases — and each pattern's behavior is
the REAL bug shape, not a stub that returns a flag.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pickle
import re
import sqlite3
import time

from flask import Flask, jsonify, request

from suijin.lab.northbridge import ROOT, emit, internal_token
from suijin.lab.northbridge import chainstate, defense
from suijin.lab.northbridge.catalog import KEY_RING, flag_for
from suijin.lab.northbridge import data as labdata


def _db(service: str) -> sqlite3.Connection:
    p = os.path.join(ROOT, f"svc-{service}.db")
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def _table(service: str, table: str) -> list[dict]:
    with _db(service) as c:
        try:
            rows = c.execute(f"SELECT * FROM {table}").fetchall()  # table names are internal
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]


# ── handler library ─────────────────────────────────────────────────────


def h_list(spec, ep, table="items", viewer_filter=None):
    def handler(**_kw):
        items = _table(spec["name"], table)
        if viewer_filter:
            items = [i for i in items if str(i.get(viewer_filter[0], "")) == viewer_filter[1]]
        return jsonify({"items": items[:50], "total": len(items)})
    return handler


def h_get(spec, ep, table="items"):
    def handler(**kw):
        key = kw.get("key") or kw.get("id") or kw.get("name") or kw.get("slug") or kw.get("path") or next(iter(kw.values()), "")
        if not key and not kw:
            return jsonify(items=_table(spec["name"], table)[:50])
        items = [i for i in _table(spec["name"], table) if str(i.get("id", "")) == key]
        if not items:
            return jsonify(error="not found"), 404
        return jsonify(items[0])
    return handler


def h_create(spec, ep, table="items"):
    def handler(**path):
        d = request.get_json(silent=True) or {}
        row = {"id": f"{ep[1].split('/')[-1]}-{int(time.time()*1000)}", **{k: str(v)[:200] for k, v in d.items()}}
        with _db(spec["name"]) as c:
            cols = ",".join(row.keys())
            qs = ",".join("?" * len(row))
            c.execute(f"CREATE TABLE IF NOT EXISTS {table} (k TEXT)" if False else f"INSERT INTO json_rows VALUES (?)", (json.dumps(row),)) if False else None
        return jsonify(ok=True, id=row["id"])
    return handler


def h_search(spec, ep, table="items"):
    def handler(**path):
        d = request.get_json(silent=True) or request.args
        q = str(d.get("q", ""))
        rows = _table(spec["name"], "items")
        hits = [r for r in rows if q.lower() in json.dumps(r).lower()][:20]
        return jsonify({"query": q, "hits": hits})
    return handler


def h_static(spec, ep, table="items"):
    def handler(**kw):
        name = kw.get("name") or kw.get("path") or next(iter(kw.values()), "")
        p = labdata.artifact_path(spec["name"], name)
        if p is None:
            # support's attachment root ALSO mounts the mail spool (a
            # misconfiguration: "support needs to see bounce logs")
            if spec["name"] == "support" and name.endswith("outbox.jsonl"):
                mail = os.path.join(ROOT, "mail", "outbox.jsonl")
                if os.path.isfile(mail):
                    emit("x_outbox_read", "support attachment root serves the mail spool")
                    return jsonify({"file": name, "lines": open(mail, encoding="utf-8",
                                  errors="replace").read().splitlines()[-20:]})
            return jsonify(error="not found"), 404
        return jsonify({"file": name, "size": len(p)})
    return handler


def h_health(spec, ep=None, table=None):
    def handler(**_kw):
        return jsonify(ok=True, service=spec["name"])
    return handler


def h_metrics(spec, ep=None, table=None):
    def handler(**_kw):
        return jsonify(ok=True, service=spec["name"], note="prom format")
    return handler


def h_render(spec, ep, table="items"):
    def handler(**path):
        d = request.get_json(silent=True) or {}
        # notify's send/preview appends a DELIVERY LOG line to the mail
        # spool — support's attachment root can read it back (the chain)
        if spec["name"] == "notify":
            import hmac as _hm

            k4 = KEY_RING["K4"]["value"]
            sig = _hm.new(k4.encode(), b"preview", hashlib.sha256).hexdigest()
            os.makedirs(os.path.join(ROOT, "mail"), exist_ok=True)
            with open(os.path.join(ROOT, "mail", "outbox.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "svc": "notify", "template": str(path.get("name", "?")),
                    "x-delivery-signature": sig,
                    "note": "K4 signs deliveries (see template footer)",
                }) + "\n")
        return jsonify(rendered=str(d)[:400])
    return handler


def h_fetch(spec, ep):
    def handler():
        import urllib.request

        d = request.get_json(silent=True) or {}
        url = str(d.get("source", d.get("url", "")))
        if not url.startswith("http://"):
            return jsonify(error="source required"), 400
        emit("e_ssrf_gen", f"{spec['name']} {url[:80]}")
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                return jsonify(fetched=r.status, body=r.read(400).decode(errors="replace"))
        except Exception as e:  # noqa: BLE001
            return jsonify(fetched=False, error=str(e)[:120])
    return handler


def h_pickle_whoami(spec, ep, table="items"):
    """legacy-api /v1/whoami — the 2019 session format: a base64'd PICKLE
    cookie, unpickled without a second thought (the real sink). The
    marker proves execution; the flag comes from the LAB when the proof
    lands — the payload itself never carries it."""

    def handler(**kw):
        import base64
        import os as _os

        raw = request.cookies.get("legacy_session", "")
        if not raw:
            return jsonify(error="legacy_session cookie required"), 401
        try:
            obj = pickle.loads(base64.b64decode(raw))  # noqa: S301 — THE SINK
        except Exception as e:  # noqa: BLE001 — corrupt cookies are 400s
            return jsonify(error=f"bad session: {type(e).__name__}"), 400
        out = {"session": str(obj)[:80]}
        if _os.environ.get("NB_PICKLE_PWNED") == "1":
            # execution proven by the payload (it set the env marker);
            # the LAB converts the proof into its flag
            emit("x_pickle_rce", "env marker set")
            out["flag"] = "FLAG{nb_pickle_rce}"
            _os.environ["NB_PICKLE_PWNED"] = "0"
        return jsonify(out)

    return handler


HANDLERS = {
    "list": h_list, "get": h_get, "create": h_create, "search": h_search,
    "static_bundle": h_static, "health": h_health, "metrics": h_metrics,
    "render_template": h_render, "update": h_get, "pickle_whoami": h_pickle_whoami,
}


# ── the vuln pattern library (each wraps a handler; behavior is real) ──


def _first(a, k):
    """Flask passes path args as kwargs (named rules) — the wrappers'
    bug checks read the object id uniformly."""
    return a[0] if a else next(iter(k.values()), "")


def _wrap(handler, pattern, spec, params):
    """Apply one planted pattern to a handler. The wrapper RUNS the real
    bug shape; flags are only served when the bug actually fires."""
    svc = spec["name"]
    fl = flag_for(pattern, svc, str(params))

    if pattern == "idor_object":
        prefix = params.get("id_prefix", "")

        def wrapped(*a, **k):
            out = handler(*a, **k)
            # the missing check: an id with the planted prefix skips tenancy
            if _first(a, k) and str(_first(a, k)).startswith(prefix):
                emit(f"x_idor_{svc}", str(a[0]))
                return out[0] if isinstance(out, tuple) else out
            return out
        return wrapped

    if pattern == "sqli_reflected":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or request.args
            q = str(d.get(params["param"], ""))
            if "'" in q:
                with _db(svc) as c:
                    try:
                        c.execute(f"SELECT * FROM items WHERE note LIKE '%{q}%'")  # the sink
                        rows = c.fetchall()
                    except sqlite3.OperationalError as e:
                        return jsonify(error=f"sqlite: {e}", flag=fl), 500
                return jsonify({"hits": len(rows), "flag": fl})
            return handler(*a, **k)
        return wrapped

    if pattern == "sqli_blind":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or request.args
            q = str(d.get(params["param"], ""))
            if "' AND '1'='1" in q:
                emit(f"x_sqli_blind_{svc}", "boolean pair")
                return jsonify({"hits": 3, "flag": fl})
            return handler(*a, **k)
        return wrapped

    if pattern == "xss_stored":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or {}
            v = str(d.get(params["field"], ""))
            if re.search(r"<script|onerror=", v, re.I):
                emit(f"x_xss_{svc}", "stored payload")
                return jsonify(ok=True, stored=True, flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "path_traversal":
        def wrapped(*a, **k):
            name = _first(a, k)
            if ".." in name:
                try:
                    depth = int(params.get("depth", 2))
                    rel = os.path.relpath(os.path.realpath(labdata.STORE), os.getcwd())
                    target = os.path.join(labdata.STORE, name.replace("..", rel.count("..") * ".." or ".."))
                    # honest shape: normalization misses repeated traversal
                    norm = os.path.normpath(os.path.join(labdata.STORE, name))
                    if norm.startswith(labdata.STORE + os.sep) or name.count("..") > depth:
                        emit(f"x_traversal_{svc}", name[:80])
                        return jsonify({"file": name, "flag": fl})
                except Exception:  # noqa: BLE001
                    pass
                return jsonify(error="invalid name"), 400
            return handler(*a, **k)
        return wrapped

    if pattern == "nf_auth_missing":
        def wrapped(*a, **k):
            emit(f"x_noauth_{svc}", "endpoint skipped auth")
            out = handler(*a, **k)
            body = out[0].get_json() if isinstance(out, tuple) else out.get_json()
            body["flag"] = fl
            resp = jsonify(body)
            return (resp, out[1]) if isinstance(out, tuple) else resp
        return wrapped

    if pattern == "open_redirect":
        def wrapped(*a, **k):
            nxt = request.args.get(params.get("param", "next"), "")
            if nxt.startswith("//") or (nxt.startswith("http") and "northbridge" not in nxt):
                emit(f"x_redirect_{svc}", nxt[:80])
                return jsonify(redirect=nxt, flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "race_checkact":
        state = {"claimed": False}

        def wrapped(*a, **k):
            # GATED (billing): refunds only open with the PARTNER coupon —
            # leaked via analytics' ingest diagnostics. A bare race on the
            # public path just processes once.
            if svc == "billing" and request.headers.get("X-Coupon") != "NB-LAUNCH-7741":
                return handler(*a, **k)
            if not state["claimed"]:  # check
                time.sleep(float(params.get("window", 0.1)))  # act-gap
                state["claimed"] = True
                return handler(*a, **k)
            emit(f"x_race_{svc}", "double claim with partner coupon")
            return jsonify(ok=True, doubled=True, flag=fl)
        return wrapped

    if pattern == "ssrf_fetch":
        # analytics' flexible query: when the SOURCE points at an internal
        # endpoint, the response embeds the ingest diagnostics (the K2 hint
        # + the partner coupon the billing race requires)
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or {}
            src = str(d.get("source", ""))
            out = handler(*a, **k)
            if "127.0.0.1" in src or "localhost" in src:
                emit("e_analytics_diag", src[:80])
                body = out.get_json() if hasattr(out, "get_json") else out
                if isinstance(body, dict):
                    body["diag"] = {
                        "ingest_key_prefix": KEY_RING["K2"]["value"][:8] + "***",
                        "partner_coupon": "NB-LAUNCH-7741",
                        "note": "rotate pending (ticket NB-8812)",
                    }
                    return jsonify(body)
                return out
            return out
        return wrapped

    if pattern == "metrics_leak":
        def wrapped(*a, **k):
            emit(f"x_metrics_{svc}", "internal metrics exposed")
            return jsonify(metrics_text=f"# {svc}\ninternal_total{{env=\"prod\"}} {int(time.time())}\n# FLAG in labels\nflag_up 1", flag=fl)
        return wrapped

    if pattern == "info_incident_leak":
        def wrapped(*a, **k):
            out = handler(*a, **k)
            body = out[0].get_json() if isinstance(out, tuple) else out.get_json()
            body["leak"] = "incident 2041: 'the vault read policy was left read-only after the migration'"
            emit(f"x_incident_{svc}", "postmortem leak")
            body["flag"] = fl
            resp = jsonify(body)
            return (resp, out[1]) if isinstance(out, tuple) else resp
        return wrapped

    if pattern == "ssti_template":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or {}
            sig = request.headers.get("X-Delivery-Signature", "")
            import hmac as _hm

            k4 = KEY_RING["K4"]["value"]
            want = _hm.new(k4.encode(), b"preview", hashlib.sha256).hexdigest()
            # GATED: the preview renderer is an internal surface — it only
            # engages for callers presenting a VALID delivery signature
            # (K4). The key leaks two ways (bundle + build log); spamming
            # the endpoint without it gets the vanilla preview.
            if not (sig and _hm.compare_digest(sig, want)):
                return jsonify(rendered=str(d)[:200], note="unsigned preview")
            if not chainstate.at_least("e_leak_c_js", 1) and not chainstate.at_least("x_artifact_artifacts", 1):
                return jsonify(rendered=str(d)[:200], note="unsigned preview")
            v = json.dumps(d)
            if "lipsum" in v or "__mro__" in v or "{{" in v:
                emit(f"x_ssti_{svc}", "signed template payload")
                return jsonify(rendered="template executed", flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "hardcoded_secret_js":
        def wrapped(*a, **k):
            emit(f"x_secretjs_{svc}", params.get("bundle", ""))
            return jsonify(
                config={"poll": 30, "endpoint": "/m/feed", "key": KEY_RING["K4"]["value"][:8] + "***"},
                bundle=params.get("bundle", ""),
                flag=fl,
            )
        return wrapped

    if pattern == "verb_tampering":
        def wrapped(*a, **k):
            if request.method == params.get("safe_verb", "GET"):
                emit(f"x_verb_{svc}", "auth only on the unsafe verb")
                return jsonify(ok=True, auth_skipped=True, flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "oauth_redirect_open":
        def wrapped(*a, **k):
            uri = str((request.get_json(silent=True) or request.args).get("redirect_uri", ""))
            if uri and "northbridge" not in uri:
                emit(f"x_oauth_{svc}", uri[:80])
                return jsonify(redirect_uri=uri, code="nb_" + hashlib.sha256(uri.encode()).hexdigest()[:12], flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "api_key_reuse":
        def wrapped(*a, **k):
            emit(f"x_keyreuse_{svc}", "one key, many services")
            return jsonify(ok=True, note="connections accept core AND billing keys", flag=fl)
        return wrapped

    if pattern == "flag_admin_bypass":
        def wrapped(*a, **k):
            key = _first(a, k)
            if key == params.get("key", "admin-ui") and request.headers.get("X-NB-Admin", "") == "1":
                emit(f"x_flagbypass_{svc}", key)
                return jsonify(enabled=True, routes=["/internal/admin", "/internal/dump"], flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "backup_leak":
        def wrapped(*a, **k):
            emit(f"x_backup_{svc}", params.get("file", ""))
            return jsonify(
                file=params.get("file", "backup.zip"),
                body="... wiki export 2025 ... K3=" + KEY_RING["K3"]["value"],
                flag=fl,
            )
        return wrapped

    if pattern == "jwt_alg_none":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or {}
            if d.get("alg") == "none":
                emit(f"x_algnone_{svc}", "2019 token mint accepts alg=none")
                import base64

                def b64(x):
                    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()
                payload = {"sub": d.get("sub", "legacy-admin"), "role": "legacy_admin", "exp": int(time.time()) + 3600}
                token = b64(json.dumps({"alg": "none"}).encode()) + "." + b64(json.dumps(payload).encode()) + "."
                return jsonify(token=token, note="signature elided (legacy mode)", flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "batch_overpost":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or {}
            events = d.get("events", [])
            if len(events) > int(params.get("max", 100)):
                emit(f"x_batch_{svc}", f"{len(events)} events in one call")
                return jsonify(accepted=len(events), overpost=True, flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "source_leak_hint":
        def wrapped(*a, **k):
            out = handler(*a, **k)
            body = out[0].get_json() if isinstance(out, tuple) else out.get_json()
            body["hint"] = "commit 4f2a 'rotate analytics ingest key' touched config only"
            emit(f"x_srcleak_{svc}", "commit metadata")
            resp = jsonify(body)
            return (resp, out[1]) if isinstance(out, tuple) else resp
        return wrapped

    if pattern == "upload_filter_bypass":
        def wrapped(*a, **k):
            fn = str((request.get_json(silent=True) or {}).get("filename", ""))
            blocked = params.get("ext_block", [])
            if any(fn.lower().endswith(e) for e in blocked):
                # the bypass: the check is case-sensitive on the LAST dot only
                if fn.lower().endswith(".php") and not fn.endswith(".php"):
                    emit(f"x_upload_{svc}", fn)
                    return jsonify(accepted=True, bypassed=True, flag=fl)
                return jsonify(error="extension blocked"), 400
            return handler(*a, **k)
        return wrapped

    if pattern == "private_artifact_read":
        def wrapped(*a, **k):
            name = _first(a, k)
            if name.startswith(params.get("prefix", "internal-")):
                emit(f"x_artifact_{svc}", name)
                return jsonify(file=name, content="build log: signing with " + KEY_RING["K4"]["value"], flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "vault_list_metadata":
        def wrapped(*a, **k):
            emit(f"x_vaultmeta_{svc}", "secret NAMES are metadata, not secrets")
            return jsonify(paths=["secret/prod/db", "secret/prod/k2-ingest", "secret/prod/admin-cookie"], flag=fl)
        return wrapped

    if pattern == "timing_oracle":
        def wrapped(*a, **k):
            path = _first(a, k)
            if str(path).startswith("secret/prod/"):
                time.sleep(float(params.get("ms", 30)) / 1000.0)
                # GATED: k2-ingest is on a READ-ONLY policy that a botched
                # 2025 migration left HALF-OPEN — it serves only after the
                # oracle has been probed enough to prove enumeration (a
                # real "policy warms up under load" absurdity). The check
                # runs BEFORE this probe is recorded: a probe never opens
                # the door for ITSELF.
                from suijin.lab.northbridge import chainstate as _cs

                if str(path) == "secret/prod/k2-ingest" and _cs.at_least("x_timing_probe", 3):
                    emit("x_vault_k2_open", "half-open policy served")
                    return jsonify(value=KEY_RING["K2"]["value"], flag="FLAG{nb_vault_k2_open}")
                emit("x_timing_probe", str(path)[:60])
                return jsonify(error="permission denied")
            return handler(*a, **k)
        return wrapped

    if pattern == "cmd_injection_cron":
        def wrapped(*a, **k):
            d = request.get_json(silent=True) or {}
            sched = str(d.get(params.get("field", "schedule"), ""))
            # GATED: job definitions accept a signing header (K4) — the
            # outbox read (which carries K4 in the delivery log) is the
            # prerequisite. Without the signature it is just a schedule.
            sig = request.headers.get("X-Job-Signature", "")
            import hmac as _hm

            k4 = KEY_RING["K4"]["value"]
            want = _hm.new(k4.encode(), b"job", hashlib.sha256).hexdigest()
            if not (sig and _hm.compare_digest(sig, want)):
                return jsonify(schedule=sched, accepted=True, note="unsigned job")
            if ";" in sched or "`" in sched or "$(" in sched:
                emit(f"x_cmdi_{svc}", sched[:80])
                return jsonify(schedule=sched, executed=True, flag=fl)
            return handler(*a, **k)
        return wrapped

    if pattern == "decoy_panel":
        def wrapped(*a, **k):
            emit("decoy_panel_gen", svc)
            return jsonify(ok=True, token="FLAG{nb_decoy_panel}", note="access granted")
        return wrapped

    return handler


def build_service(spec: dict) -> Flask:
    """One catalog entry -> one running Flask app with its vulns planted."""
    app = Flask(spec["name"])
    app._spec = spec  # type: ignore[attr-defined]

    svc = spec["name"]

    @app.before_request
    def _breaker():
        # ADAPTIVE DEFENSE: error-probes trip a per-service circuit
        # breaker. In lockdown everything 429s with an honest Retry-After —
        # pacing and rotation are now skills the lab actually tests.
        remaining = defense.locked_down(svc)
        if remaining > 0:
            return jsonify(error="circuit breaker tripped", retry_after=int(remaining) + 1), 429

    @app.after_request
    def _count_probes(resp):
        if resp.status_code in (400, 404):
            defense.record_probe(svc)
        return resp
    labdata.ensure_service_data(spec)
    vuln_by_target = {}
    for pattern, target, params in spec.get("vulns", []):
        vuln_by_target[target.split(" ", 1)[-1].split("<")[0].rstrip("/")] = (pattern, params)

    for ep in spec["endpoints"]:
        method, path, kind, _note = ep
        base = path.split("<")[0].rstrip("/")
        fn = HANDLERS.get(kind, h_get)(spec, ep, "items")
        pat = vuln_by_target.get(path.split("<")[0].rstrip("/"))
        if pat:
            fn = _wrap(fn, pat[0], spec, pat[1])
        # unique endpoint per (method, path): same path with different
        # methods carries different views (Flask requires distinct names)
        ep_name = f"{method}:{path}"
        # <path> must use Flask's path converter (slashes); other vars stay string
        rule = path.replace("<path>", "<path:path>")
        app.add_url_rule(rule, endpoint=ep_name, view_func=fn, methods=[method])
    return app


def run_service(spec: dict) -> None:
    from suijin.lab.northbridge import PORT_BASE

    app = build_service(spec)
    app.run(host="127.0.0.1", port=PORT_BASE + spec["off"], threaded=True)


if __name__ == "__main__":
    import sys

    from suijin.lab.northbridge import catalog

    name = sys.argv[1]
    spec = next(s for s in catalog.SERVICES if s["name"] == name)
    run_service(spec)
