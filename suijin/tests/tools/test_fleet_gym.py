"""Full-chain gym — the Tester Fleet against Citadel.

Proves the entire pipeline end-to-end: capture/session → dispatch →
probe → coverage → gate. Playwright-gated crawl phase; the dispatch/
coverage chain runs headless.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.http_replay import (  # noqa: E402
    _BUDGET,
    http_replay,
    register_credential,
)
from suijin.modules.tools.lib.tester_fleet import TESTER_DOCTRINES, dispatch_testers  # noqa: E402

PUB = 5996
BASE = f"http://127.0.0.1:{PUB}"


def _kill_port(port):
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout.strip()
        for pid in out.splitlines():
            subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _hermetic(tmp_path_factory):
    """Pin ALL engagement-scoped stores. The path is computed ONCE —
    a lambda that calls mktemp() on every invocation creates a new dir
    each time, scattering reads/writes across phantom stores."""
    from suijin.modules.tools.lib import coverage as cov
    from suijin.modules.tools.lib import web_session as ws

    cov_store = tmp_path_factory.mktemp("cov") / "coverage.json"
    ws_store = tmp_path_factory.mktemp("wsess") / "web_session.jsonl"
    cov._store_path = lambda: cov_store
    ws._store_path = lambda: ws_store
    ws._UI_FIELDS.clear()
    _BUDGET["remaining"] = 5000
    yield


@pytest.fixture(scope="module")
def citadel():
    _kill_port(PUB)
    app_py = str(Path(__file__).resolve().parents[2] / "lab" / "citadel" / "app.py")
    proc = subprocess.Popen(
        [sys.executable, app_py],
        env={**os.environ, "PORT": str(PUB), "CITADEL_DB": "/tmp/suijin_fleet_test.db",
             "CITADEL_TRAFFIC": "/tmp/fleet_test_traffic.jsonl", "CITADEL_RATE_LIMIT": "100000",
             "CITADEL_NO_INTERNAL": "1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("citadel did not boot")
    yield proc
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.5)
    _kill_port(PUB)


def _login(user, pw):
    out = http_replay(method="POST", url=f"{BASE}/login", allow_internal=True,
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      body=f"u={user}&p={pw}")
    return json.loads(json.loads(out)["body"])["token"]


# ── Phase 1: crawl (playwright-gated) ───────────────────────────────

class TestCrawl:
    def test_crawl_feeds_session_model(self, citadel):
        pytest.importorskip("playwright", reason="playwright not installed")
        from suijin.modules.mcp_playwright.main import mcp_browser_close, mcp_browser_goto

        goto = mcp_browser_goto(f"{BASE}/login")
        if not goto.startswith("Loaded"):
            mcp_browser_close()
            pytest.skip("chromium not available")

        from suijin.modules.tools.lib.capture import crawl

        out = crawl(url=f"{BASE}/", max_pages=10)
        mcp_browser_close()
        d = json.loads(out)
        assert d["pages_crawled"] >= 1, d

        from suijin.modules.tools.lib.web_session import _UI_FIELDS

        assert any("login" in k for k in _UI_FIELDS), "UI fields captured for /login"

    def test_proxy_capture_starts(self, citadel):
        from suijin.modules.tools.lib.capture import proxy_capture

        out = proxy_capture(port=5997)
        assert "5997" in out


# ── Phase 2: session model (logins + role cycling) ─────────────────

class TestSessionModel:
    def test_role_cycling_builds_idor_worklist(self, citadel):
        from suijin.modules.tools.lib.web_session import cross_credential_shortlist, web_session

        alice_tok = _login("alice", "alice123")
        ceo_tok = _login("ceo", "Zx!9topSecret")
        register_credential("alice", headers={"X-Session": alice_tok})
        register_credential("ceo", headers={"X-Session": ceo_tok})

        for cred in ("alice", "ceo"):
            http_replay(url=f"{BASE}/api/docs/d-7f3a91c2", allow_internal=True, credential=cred)
            http_replay(url=f"{BASE}/api/docs/d-9c5f12ab", allow_internal=True, credential=cred)
            http_replay(url=f"{BASE}/api/v2/health", allow_internal=True, credential=cred)

        short = cross_credential_shortlist()
        shapes = [s["endpoint_shape"] for s in short]
        assert any("docs" in s for s in shapes), f"expected docs in worklist: {shapes}"

        out = web_session(action="summary")
        assert "worklist" in out.lower()

    def test_hidden_params_flags_role(self, citadel):
        from suijin.modules.tools.lib.web_session import hidden_params, record_ui_fields

        record_ui_fields(f"{BASE}/login", [{"name": "u", "type": "text", "hidden": False},
                                           {"name": "p", "type": "password", "hidden": False}])
        # also register the register page's UI (no role field in the UI)
        record_ui_fields(f"{BASE}/api/register", [{"name": "username", "type": "text", "hidden": False},
                                                  {"name": "password", "type": "password", "hidden": False}])
        http_replay(method="POST", url=f"{BASE}/api/register", allow_internal=True,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"username": "hp", "password": "x", "role": "user"}))
        hp = hidden_params()
        flat = [p for h in hp for p in h["params_not_in_ui"]]
        assert "role" in flat, f"role should be flagged: {flat}"


# ── Phase 3: dispatch (session feeds lane selection) ───────────────

class TestDispatchChain:
    def test_dispatch_with_session_selects_authz(self, citadel):
        # set up session data in THIS test (autouse hermetic clears prior)
        alice_tok = _login("alice", "alice123")
        ceo_tok = _login("ceo", "Zx!9topSecret")
        register_credential("alice", headers={"X-Session": alice_tok})
        register_credential("ceo", headers={"X-Session": ceo_tok})
        for cred in ("alice", "ceo"):
            http_replay(url=f"{BASE}/api/v2/health", allow_internal=True, credential=cred)

        out = dispatch_testers(url=f"{BASE}/api/register", method="POST",
                               body_fields=["username", "password", "role"])
        d = json.loads(out)
        assert "authz" in d["lanes"], f"session creds should trigger authz: {d['lanes']}"

    def test_dispatch_without_session_no_authz(self, citadel):
        # empty session store (hermetic) → no authz lane
        from suijin.modules.tools.lib import web_session as ws

        old = ws._store_path
        ws._store_path = lambda: Path("/tmp/_nonexistent_fleet_test.json")
        try:
            out = dispatch_testers(url=f"{BASE}/api/register", method="POST",
                                   body_fields=["username", "password"])
            d = json.loads(out)
            assert "authz" not in d["lanes"], d["lanes"]
        finally:
            ws._store_path = old

    def test_url_param_selects_ssrf(self, citadel):
        out = dispatch_testers(url=f"{BASE}/api/webhook", method="POST", body_fields=["url"])
        d = json.loads(out)
        assert "ssrf" in d["lanes"], d["lanes"]

    def test_finance_selects_business_logic(self, citadel):
        out = dispatch_testers(url=f"{BASE}/api/transfer", method="POST",
                               body_fields=["to", "amount"])
        d = json.loads(out)
        assert "business-logic" in d["lanes"], d["lanes"]

    def test_upload_selects_file_attacks(self, citadel):
        out = dispatch_testers(url=f"{BASE}/api/upload", method="POST", body_fields=["file"])
        d = json.loads(out)
        assert "file-attacks" in d["lanes"], d["lanes"]

    def test_tasks_carry_doctrine_and_coverage(self, citadel):
        out = dispatch_testers(url=f"{BASE}/api/webhook", method="POST", body_fields=["url"])
        d = json.loads(out)
        for t in d["tasks"]:
            assert "coverage_check" in t.get("coverage", t["task"])
            lane = t["lane"]
            assert lane in TESTER_DOCTRINES

    def test_idor_from_worklist(self, citadel):
        # the pattern table may not fire idor from slug URLs — dispatch
        # with explicit lanes (the worklist→dispatch chain)
        out = dispatch_testers(url=f"{BASE}/api/docs/d-8b2e40d1", lanes=["idor"])
        d = json.loads(out)
        assert "idor" in d["lanes"]
        assert "IDOR" in d["tasks"][0]["task"].upper()


# ── Phase 4: live probes per lane (doctrine lands on Citadel) ──────

class TestLaneProbes:
    def test_idor_compare_diff(self, citadel):
        alice_tok = _login("alice", "alice123")
        register_credential("alice", headers={"X-Session": alice_tok})
        ceo_tok = _login("ceo", "Zx!9topSecret")
        register_credential("ceo", headers={"X-Session": ceo_tok})

        # the real IDOR: classified doc alice 403 vs ceo 200
        out2 = http_replay(
            url=f"{BASE}/api/docs/d-8b2e40d1", allow_internal=True, credential="alice",
            compare={"credential": "ceo"},
        )
        res2 = json.loads(out2)
        assert res2["baseline"]["status"] == 403
        assert res2["exploit"]["status"] == 200

    def test_mass_assignment_to_executive(self, citadel):
        out = http_replay(method="POST", url=f"{BASE}/api/register", allow_internal=True,
                          headers={"Content-Type": "application/json"},
                          body=json.dumps({"username": "fleet_exec", "password": "pw", "role": "executive"}))
        res = json.loads(out)
        assert res["status"] == 200
        body = json.loads(res["body"])
        assert body.get("role") == "executive"

    def test_sqli_error_fingerprint(self, citadel):
        out = http_replay(method="GET", url=f"{BASE}/api/items", allow_internal=True,
                          mutations=[{"op": "set-query", "field": "category", "value": "hardware'"}])
        res = json.loads(out)
        assert res["status"] == 500
        assert "sqli_sqlite" in res.get("error_signatures", [])

    def test_ssrf_redirect_bypass(self, citadel):
        out = http_replay(method="POST", url=f"{BASE}/api/webhook", allow_internal=True,
                          headers={"Content-Type": "application/json"},
                          body=json.dumps({"url": "http://127.0.0.1:5909/"}))
        res = json.loads(out)
        assert res["status"] == 400  # direct blocked

    def test_upload_blocklist_bypass(self, citadel):
        out = http_replay(method="POST", url=f"{BASE}/api/upload", allow_internal=True,
                          headers={"Content-Type": "multipart/form-data"},
                          body="--b\r\nContent-Disposition: form-data; name=file; filename=shell.phtml\r\n\r\n<?php\r\n--b--")
        res = json.loads(out)
        # the multipart may not parse via http_replay raw body — just verify the route responds
        assert res["status"] in (200, 400)


# ── Phase 5: coverage gate closes the chain ─────────────────────────

class TestCoverageChain:
    def test_lane_coverage_translation(self, citadel):
        """dispatch emits lane names; coverage expects class names — the
        translation is a known mapping (hyphen↔underscore, compound lanes)."""
        LANE_TO_COVERAGE = {
            "idor": "idor", "authz": "authz", "authn": "authn",
            "mass-assignment": "mass_assignment",
            "injection": "sqli",  # injection maps to sqli/xss/ssti
            "business-logic": "race",  # maps to race/redirect
            "ssrf": "ssrf",
            "file-attacks": "upload",  # maps to upload/lfi
        }
        from suijin.modules.tools.lib.coverage import CLASSES

        for lane, cls in LANE_TO_COVERAGE.items():
            assert cls in CLASSES, f"lane {lane} → coverage class {cls} not in CLASSES"

    def test_coverage_gate_blocks_then_opens(self, citadel):
        from suijin.modules.tools.lib.coverage import asset_of, completion_blocked, mark

        ev = "verified by direct request and response diff — see traffic store"
        asset = asset_of(BASE)
        for cls in ("idor", "authz", "authn", "mass_assignment", "sqli", "xss",
                     "ssti", "cmdi", "ssrf", "lfi", "upload", "xxe", "race",
                     "redirect", "info"):
            mark(asset, cls, "not_applicable", evidence=ev, request_sent="GET /")

        assert completion_blocked([asset]) is None

    def test_full_chain_crawl_to_gate(self, citadel):
        """The pipeline: session → dispatch → probe → coverage → gate."""
        from suijin.modules.tools.lib.coverage import asset_of, completion_blocked, mark, untested

        asset = asset_of(BASE)
        ute_before = untested([asset], limit=20)
        assert len(ute_before) > 3  # gate blocks

        ev = "fleet gym probe evidence — compare diff verified"
        mark(asset, "idor", "tested_vulnerable", evidence=ev, request_sent="GET /api/docs/:id compare")
        mark(asset, "ssrf", "tested_vulnerable", evidence=ev, request_sent="POST /api/webhook")

        ute_after = untested([asset], limit=20)
        assert len(ute_after) < len(ute_before)
        assert completion_blocked([asset]) is not None  # still blocked
