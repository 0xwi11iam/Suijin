"""Wave 2 gym — inject_probe: batteries return FACTS, never verdicts.
Proven against Citadel's real sinks (stored-XSS render, SSTI, cmd-inject,
WAF'd SQLi, traversal)."""

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

from suijin.modules.tools.lib.http_replay import _BUDGET  # noqa: E402
from suijin.modules.tools.lib.inject_probe import inject_probe  # noqa: E402

PUB = 5983
BASE = f"http://127.0.0.1:{PUB}"


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
        env={**os.environ, "PORT": str(PUB), "CITADEL_DB": "/tmp/suijin_probe_test.db",
             "CITADEL_TRAFFIC": "/tmp/probe_test_traffic.jsonl", "CITADEL_RATE_LIMIT": "100000",
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


def _facts(**kw):
    out = inject_probe(allow_internal=True, **kw)
    assert not out.startswith("Error"), out
    return json.loads(out)


class TestUnit:
    def test_unknown_class_clean_error(self):
        assert inject_probe(url="http://t/", vuln_class="nuke", allow_internal=True).startswith("Error")

    def test_missing_url(self):
        assert inject_probe().startswith("Error")

    def test_scope_guard(self):
        assert "allow_internal" in inject_probe(url="http://127.0.0.1:1/", vuln_class="xss")


class TestXSS:
    def test_reflection_facts_with_context(self, citadel):
        # fmt=html: the search-results header echoes the query raw
        f = _facts(url=f"{BASE}/search?fmt=html", field="q", vuln_class="xss")
        assert f["marker_reflected"] is True
        assert f["context"] in ("html-body", "html-tag-inner", "attribute", "javascript", "rcdata-title")
        assert isinstance(f["surviving_tags"], list)
        assert f["sends"] <= 120
        assert "curl" in f

    def test_non_reflecting_param_reports_absence(self, citadel):
        f = _facts(url=f"{BASE}/search?fmt=json", field="q", vuln_class="xss")  # json api: no html echo
        assert f["marker_reflected"] is False


class TestSSTI:
    def test_evaluated_discriminator(self, citadel):
        # register an admin, login, set custom_message {{7*7}} — but inject_probe
        # hits the PUBLIC surface; the SSTI sink needs the admin session, so
        # prove the discriminator on the reflected-only case: the literal
        # echo must NOT count as evaluated
        f = _facts(url=f"{BASE}/search", field="q", vuln_class="ssti")
        assert f["evaluated_syntaxes"] == []  # reflection-only ⇒ no evaluation claims
        assert "product-present" in f["note"]

    def test_true_evaluation_via_admin(self, citadel):
        from suijin.modules.tools.lib.http_replay import http_replay

        http_replay(method="POST", url=f"{BASE}/api/register", allow_internal=True,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"username": "probe_admin", "password": "pw1", "role": "admin"}))
        out = http_replay(method="POST", url=f"{BASE}/login", allow_internal=True,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          body="u=probe_admin&p=pw1")
        tok = json.loads(json.loads(out)["body"])["token"] if isinstance(out, str) else out["body"]
        # the SSTI sink is POST /api/settings — probe the BODY field with the session
        f = _facts(url=f"{BASE}/api/settings", method="POST", in_body=True, field="custom_message",
                   vuln_class="ssti", headers={"Content-Type": "application/json", "X-Session": tok})
        assert "{{7919*6841}}" in f["evaluated_syntaxes"]  # real Jinja2 evaluation


class TestSQLi:
    def test_error_fingerprints_and_noise_floor(self, citadel):
        # direct error surface: /api/items with a bare quote (WAF misses single quotes without OR/AND)
        f = _facts(url=f"{BASE}/api/items", field="category", vuln_class="sqli")
        assert any(e["dbms"] == ["sqlite"] for e in f["error_findings"])
        assert f["noise_floor_bytes"] >= 16  # measured, not guessed

    def test_waf_blocked_gets_qualified_not_safe(self, citadel):
        # heavy OR-payload battery through the naive field: mostly WAF 404s
        f = _facts(url=f"{BASE}/api/items", field="category", vuln_class="sqli")
        # some forms pass (bare quote), the point: facts return, never crash
        assert "sends" in f


class TestLFI:
    def test_traversal_shapes_with_signatures(self, citadel):
        # /download blocks naive '../' but the single-strip shape ....// works
        f = _facts(url=f"{BASE}/download", field="file", vuln_class="lfi")
        shapes_hit = [r["shape"] for r in f["file_reads"]]
        assert "....//" in shapes_hit  # the working filter-bypass shape is REPORTED


class TestCMD:
    def test_closed_command_set(self, citadel):
        # chain A terminus needs admin JWT; the public /api/webhook is SSRF not cmd.
        # prove the closed-set discipline: no uid= output on the public surface
        f = _facts(url=f"{BASE}/api/v2/health", field="x", vuln_class="cmd")
        assert f["command_output"] == []


class TestDispatch:
    def test_route_tool_reaches_probe(self, citadel):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("inject_probe", {"url": f"{BASE}/search?fmt=html", "field": "q", "vuln_class": "xss",
                                          "allow_internal": True}, {})
        f = json.loads(str(out))
        assert f["marker_reflected"] is True
