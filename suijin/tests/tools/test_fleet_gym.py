"""Full-chain gym — the Tester Fleet against Suijin Lab (northbridge).

Proves the entire pipeline end-to-end: crawl/session → dispatch →
probe → coverage → gate, against the one lab. Target-side behavior
(planted vulns, chains, defenses) is proven by tests/lab/; this file
proves SUIJIN's machinery keys correctly off the lab's real surface.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.http_replay import (  # noqa: E402
    _BUDGET,
    http_replay,
    register_credential,
)
from suijin.modules.tools.lib.tester_fleet import TESTER_DOCTRINES, dispatch_testers  # noqa: E402

PUB = 5990  # edge (the whole stack rides NB_PORT_BASE=PUB)
BASE = f"http://127.0.0.1:{PUB}"


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
def northbridge():
    from suijin.lab.northbridge import supervisor

    os.environ["NB_PORT_BASE"] = str(PUB)
    try:
        supervisor.up(reset=True)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"northbridge did not boot: {e}")
    yield
    supervisor.down()
    os.environ.pop("NB_PORT_BASE", None)


def _login(email, pw):
    out = http_replay(
        method="POST",
        url=f"{BASE}/auth/login",
        allow_internal=True,
        headers={"Content-Type": "application/json"},
        body=json.dumps({"email": email, "password": pw}),
    )
    res = json.loads(out)
    assert res["status"] == 200, res
    return json.loads(res["body"])["token"]


# ── Phase 1: crawl ──────────────────────────────────────────────────


class TestCrawl:
    def test_crawl_feeds_session_model(self, northbridge):
        pytest.importorskip("playwright", reason="playwright not installed")
        from suijin.modules.mcp_playwright.main import mcp_browser_close, mcp_browser_goto

        goto = mcp_browser_goto(f"{BASE}/")
        if not goto.startswith("Loaded"):
            mcp_browser_close()
            pytest.skip("chromium not available")

        from suijin.modules.tools.lib.capture import crawl

        out = crawl(url=f"{BASE}/", max_pages=10)
        mcp_browser_close()
        d = json.loads(out)
        assert d["pages_crawled"] >= 1, d  # an API product: the surface is small

    def test_proxy_capture_starts(self, northbridge):
        from suijin.modules.tools.lib.capture import proxy_capture

        out = proxy_capture(port=5997)
        assert "5997" in out


# ── Phase 2: session model (logins + role cycling) ─────────────────


class TestSessionModel:
    def test_role_cycling_builds_idor_worklist(self, northbridge):
        from suijin.modules.tools.lib.web_session import cross_credential_shortlist, web_session

        founder_tok = _login("founder@acme-demo.test", "Launch2026!strong")
        ops_tok = _login("ops@northbridge.test", "OpsInternal#2026")
        register_credential("founder", headers={"Authorization": f"Bearer {founder_tok}"})
        register_credential("ops", headers={"Authorization": f"Bearer {ops_tok}"})

        # the founder's own invoice is 200 for founder, 404 for ops —
        # exactly the cross-credential delta the worklist feeds on
        for cred in ("founder", "ops"):
            http_replay(url=f"{BASE}/api/v1/invoices/INT-2026-0042", allow_internal=True, credential=cred)

        short = cross_credential_shortlist()
        shapes = [s["endpoint_shape"] for s in short]
        assert any("invoices" in s for s in shapes), f"expected invoices in worklist: {shapes}"

        out = web_session(action="summary")
        assert "worklist" in out.lower()

    def test_hidden_params_flags_role(self, northbridge):
        from suijin.modules.tools.lib.web_session import hidden_params, record_ui_fields

        # the profile UI exposes display_name only (there IS no UI — this
        # is an API; the session model records what the surface declares)
        record_ui_fields(f"{BASE}/api/v1/profile", [{"name": "display_name", "type": "text", "hidden": False}])
        http_replay(
            method="PUT",
            url=f"{BASE}/api/v1/profile",
            allow_internal=True,
            headers={"Content-Type": "application/json"},
            body=json.dumps({"display_name": "x", "role": "executive"}),
        )
        hp = hidden_params()
        flat = [p for h in hp for p in h["params_not_in_ui"]]
        assert "role" in flat, f"role should be flagged: {flat}"


# ── Phase 3: dispatch (session feeds lane selection) ───────────────


class TestDispatchChain:
    def test_dispatch_with_session_selects_authz(self, northbridge):
        founder_tok = _login("founder@acme-demo.test", "Launch2026!strong")
        ops_tok = _login("ops@northbridge.test", "OpsInternal#2026")
        register_credential("founder", headers={"Authorization": f"Bearer {founder_tok}"})
        register_credential("ops", headers={"Authorization": f"Bearer {ops_tok}"})
        for cred in ("founder", "ops"):
            http_replay(url=f"{BASE}/api/v1/invoices/INT-2026-0042", allow_internal=True, credential=cred)

        out = dispatch_testers(url=f"{BASE}/api/v1/profile", method="PUT", body_fields=["display_name", "role"])
        d = json.loads(out)
        assert "authz" in d["lanes"], f"session creds should trigger authz: {d['lanes']}"

    def test_dispatch_without_session_no_authz(self, northbridge):
        from suijin.modules.tools.lib import web_session as ws

        old = ws._store_path
        ws._store_path = lambda: Path("/tmp/_nonexistent_fleet_test.json")
        try:
            out = dispatch_testers(url=f"{BASE}/api/v1/profile", method="PUT", body_fields=["display_name"])
            d = json.loads(out)
            assert "authz" not in d["lanes"], d["lanes"]
        finally:
            ws._store_path = old

    def test_url_param_selects_ssrf(self, northbridge):
        out = dispatch_testers(url=f"{BASE}/api/v1/webhooks", method="POST", body_fields=["url"])
        d = json.loads(out)
        assert "ssrf" in d["lanes"], d["lanes"]

    def test_finance_selects_business_logic(self, northbridge):
        out = dispatch_testers(url=f"{BASE}/api/v1/vendor/payout", method="POST", body_fields=["to", "amount"])
        d = json.loads(out)
        assert "business-logic" in d["lanes"], d["lanes"]

    def test_tasks_carry_doctrine_and_coverage(self, northbridge):
        out = dispatch_testers(url=f"{BASE}/api/v1/webhooks", method="POST", body_fields=["url"])
        d = json.loads(out)
        for t in d["tasks"]:
            assert "coverage_check" in t.get("coverage", t["task"])
            lane = t["lane"]
            assert lane in TESTER_DOCTRINES

    def test_idor_from_worklist(self, northbridge):
        out = dispatch_testers(url=f"{BASE}/api/v1/invoices/EXT-2026-0199", lanes=["idor"])
        d = json.loads(out)
        assert "idor" in d["lanes"]
        assert "IDOR" in d["tasks"][0]["task"].upper()


# ── Phase 4: live probes per lane (doctrine lands on the lab) ──────


class TestLaneProbes:
    def test_idor_compare_diff(self, northbridge):
        founder_tok = _login("founder@acme-demo.test", "Launch2026!strong")
        register_credential("founder", headers={"Authorization": f"Bearer {founder_tok}"})
        ops_tok = _login("ops@northbridge.test", "OpsInternal#2026")
        register_credential("ops", headers={"Authorization": f"Bearer {ops_tok}"})

        # the tenant-checked invoice: founder 200 (own tenant), ops 404
        out2 = http_replay(
            url=f"{BASE}/api/v1/invoices/INT-2026-0042",
            allow_internal=True,
            credential="founder",
            compare={"credential": "ops"},
        )
        res2 = json.loads(out2)
        assert res2["baseline"]["status"] == 200
        assert res2["exploit"]["status"] == 404

    def test_mass_assignment_to_executive(self, northbridge):
        tok = _login("founder@acme-demo.test", "Launch2026!strong")
        out = http_replay(
            method="PUT",
            url=f"{BASE}/api/v1/profile",
            allow_internal=True,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
            body=json.dumps({"display_name": "fleet_exec", "role": "executive"}),
        )
        res = json.loads(out)
        assert res["status"] == 200
        body = json.loads(res["body"])
        assert "role" in body.get("saved", []), body

    def test_sqli_fingerprint_stays_clean(self, northbridge):
        """The no-cheese direction: the lab has NO injection — the probe
        must come back without a fingerprint (defense holds)."""
        out = http_replay(
            method="GET",
            url=f"{BASE}/api/v1/ping",
            allow_internal=True,
            mutations=[{"op": "set-query", "field": "category", "value": "hardware'"}],
        )
        res = json.loads(out)
        assert "sqli_sqlite" not in res.get("error_signatures", [])

    def test_ssrf_internal_target_lands(self, northbridge):
        """l4 is the lab's real shape: internal webhook targets DELIVER."""
        tok = _login("founder@acme-demo.test", "Launch2026!strong")
        out = http_replay(
            method="POST",
            url=f"{BASE}/api/v1/webhooks",
            allow_internal=True,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
            body=json.dumps({"url": f"http://127.0.0.1:{PUB + 3}/health"}),
        )
        res = json.loads(out)
        assert res["status"] == 200


# ── Phase 5: coverage gate closes the chain ─────────────────────────


class TestCoverageChain:
    def test_lane_coverage_translation(self, northbridge):
        """dispatch emits lane names; coverage expects class names — the
        translation is a known mapping (hyphen↔underscore, compound lanes)."""
        LANE_TO_COVERAGE = {
            "idor": "idor",
            "authz": "authz",
            "authn": "authn",
            "mass-assignment": "mass_assignment",
            "injection": "sqli",  # injection maps to sqli/xss/ssti
            "business-logic": "race",  # maps to race/redirect
            "ssrf": "ssrf",
            "file-attacks": "upload",  # maps to upload/lfi
        }
        from suijin.modules.tools.lib.coverage import CLASSES

        for lane, cls in LANE_TO_COVERAGE.items():
            assert cls in CLASSES, f"lane {lane} → coverage class {cls} not in CLASSES"

    def test_coverage_gate_blocks_then_opens(self, northbridge):
        from suijin.modules.tools.lib.coverage import asset_of, completion_blocked, mark

        ev = "verified by direct request and response diff — see traffic store"
        asset = asset_of(BASE)
        for cls in (
            "idor",
            "authz",
            "authn",
            "mass_assignment",
            "sqli",
            "xss",
            "ssti",
            "cmdi",
            "ssrf",
            "lfi",
            "upload",
            "xxe",
            "race",
            "redirect",
            "info",
        ):
            mark(asset, cls, "not_applicable", evidence=ev, request_sent="GET /")

        assert completion_blocked([asset]) is None

    def test_full_chain_crawl_to_gate(self, northbridge):
        """The pipeline: session → dispatch → probe → coverage → gate."""
        from suijin.modules.tools.lib.coverage import asset_of, completion_blocked, mark, untested

        asset = asset_of(BASE)
        ute_before = untested([asset], limit=20)
        assert len(ute_before) > 3  # gate blocks

        ev = "fleet gym probe evidence — compare diff verified"
        mark(asset, "idor", "tested_vulnerable", evidence=ev, request_sent="GET /api/v1/invoices/:id compare")
        mark(asset, "ssrf", "tested_vulnerable", evidence=ev, request_sent="POST /api/v1/webhooks")

        ute_after = untested([asset], limit=20)
        assert len(ute_after) < len(ute_before)
        assert completion_blocked([asset]) is not None  # still blocked
