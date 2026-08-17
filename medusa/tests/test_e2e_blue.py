"""End-to-end integration tests — live Flask lab + blue team defense pipeline.

Boots the real vulnerable app on a random port, fires real HTTP attacks,
feeds traffic through the LiveFeed tier router, and asserts the defense
pipeline works end-to-end:
    request → traffic log → pattern detection → flag → tarpit → real delay

CI-safe: random ports, temp files, mocked AI (no API key needed).
"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _free_port() -> int:
    """Find a free TCP port."""
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    """Poll until the server responds."""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def lab_server():
    """Boot the vulnerable Flask lab on a random port with isolated temp files."""
    from medusa.lab.blue_target import vulnerable_app as va

    tmpdir = tempfile.mkdtemp(prefix="medusa_e2e_")
    port = _free_port()

    # Isolate hardcoded paths so tests never touch the real /tmp files
    orig_tarpit = va.TARPIT_FILE
    orig_log = va.TRAFFIC_LOG
    orig_db = va.DB
    va.TARPIT_FILE = os.path.join(tmpdir, "blue_tarpit.json")
    va.TRAFFIC_LOG = os.path.join(tmpdir, "traffic.jsonl")
    va.DB = os.path.join(tmpdir, "blue_defend.db")
    va.init_db()  # re-init schema against the isolated DB

    # Start Flask in a background thread via werkzeug make_server (clean shutdown)
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", port, va.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    ready = _wait_for_server(base_url + "/health")
    if not ready:
        server.shutdown()
        raise RuntimeError("Lab server failed to start")

    yield {
        "base_url": base_url,
        "port": port,
        "tarpit_file": va.TARPIT_FILE,
        "traffic_log": va.TRAFFIC_LOG,
        "tmpdir": tmpdir,
    }

    # Cleanup
    server.shutdown()
    thread.join(timeout=5)
    va.TARPIT_FILE = orig_tarpit
    va.TRAFFIC_LOG = orig_log
    va.DB = orig_db
    import contextlib
    with contextlib.suppress(OSError):
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
    with contextlib.suppress(OSError):
        os.rmdir(tmpdir)


@pytest.fixture
def blue_stack():
    """Build a LiveFeed stack with a mocked AI engine (no API keys needed)."""
    from medusa.core.blue.ai_engine import AIAnalysisResult, BlueAIEngine
    from medusa.core.blue.subagent_manager import SubagentManager
    from medusa.core.blue.tui.feed import FeedConfig, LiveFeed

    engine = BlueAIEngine(config={})

    # Mock analyze_request — returns FLAGGED DECEIVE without any LLM call.
    # Only flags requests that actually look malicious (realistic AI behavior).
    async def mock_analyze(request, endpoint_info, subagent_notes="", request_id=0):
        text = str(request.get("body", "")) + str(request.get("user_agent", ""))
        malicious = ("' OR '1'='1" in text or "OR 1=1" in text
                     or "sqlmap" in text.lower() or "<script>" in text
                     or "union select" in text.lower())
        if malicious:
            return AIAnalysisResult(
                request_id=request_id,
                method=request.get("method", "GET"),
                path=request.get("path", "/"),
                ip=request.get("ip", "127.0.0.1"),
                body=request.get("body", ""),
                headers=request.get("headers", {}),
                query=request.get("query", {}),
                verdict="FLAGGED",
                score=9,
                action="DECEIVE",
                attack_analysis="Mocked SQLi analysis",
                reasoning="Mocked — test double",
                attacker_assessment="Automated scanner",
            )
        return AIAnalysisResult(
            request_id=request_id,
            method=request.get("method", "GET"),
            path=request.get("path", "/"),
            ip=request.get("ip", "127.0.0.1"),
            body=request.get("body", ""),
            headers=request.get("headers", {}),
            query=request.get("query", {}),
            verdict="NOT FLAGGED",
            score=1,
            action="LOG",
            reasoning="Clean request — test double",
        )

    engine.analyze_request = mock_analyze

    subagents = SubagentManager(config={}, target_path="")
    feed = LiveFeed(
        ai_engine=engine,
        subagent_manager=subagents,
        config=FeedConfig(baseline_requests=3, ai_analysis_enabled=True),
    )
    return feed


def _read_last_log_entry(log_path: str) -> dict:
    """Read the last line of the traffic JSONL log."""
    with open(log_path) as f:
        lines = f.readlines()
    assert lines, "Traffic log is empty"
    return json.loads(lines[-1])


@pytest.fixture
def isolated_tarpit_path(lab_server, monkeypatch):
    """Point the LiveFeed's class-level tarpit file at the isolated tmpdir."""
    from medusa.core.blue.tui.feed import LiveFeed
    monkeypatch.setattr(LiveFeed, "TARPIT_FILE", lab_server["tarpit_file"])
    yield lab_server["tarpit_file"]


@pytest.fixture(autouse=True)
def clean_tarpit_state(lab_server):
    """Reset tarpit state before every test so delays don't leak across tests."""
    tarpit_file = lab_server["tarpit_file"]
    if os.path.exists(tarpit_file):
        os.unlink(tarpit_file)
    yield
    if os.path.exists(tarpit_file):
        os.unlink(tarpit_file)


class TestBlueTeamE2E:
    """Full pipeline: real HTTP attack → detection → tarpit → real delay."""

    def test_sqli_attack_detected_and_tarpitted(self, lab_server, blue_stack, isolated_tarpit_path):
        """Fire a real SQLi at the lab, assert detection and tarpit deployment."""
        import requests as req

        feed = blue_stack
        base = lab_server["base_url"]

        # ── Phase 1: establish baseline with clean traffic ──
        for i in range(3):
            clean = {
                "method": "GET", "path": f"/api/users/{i}",
                "ip": f"10.0.0.{i+1}", "body": "", "query": {},
                "user_agent": "test-client", "headers": {},
            }
            asyncio.run(feed.process_request(clean))
        assert feed.baseline_established, "Baseline should be established after 3 requests"

        # ── Phase 2: fire a real SQLi attack at the Flask lab ──
        attack_payload = {"username": "admin' OR '1'='1", "password": "x"}
        resp = req.post(
            f"{base}/auth/login",
            json=attack_payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert resp.status_code in (200, 401, 403), f"Unexpected status {resp.status_code}"

        # The Flask before_request hook logged it to the traffic log
        log_entry = _read_last_log_entry(lab_server["traffic_log"])
        assert log_entry["method"] == "POST"
        assert "OR '1'='1" in str(log_entry.get("body", ""))

        # ── Phase 3: feed the logged request through the LiveFeed pipeline ──
        request_for_feed = {
            "method": log_entry["method"],
            "path": log_entry["path"],
            "body": log_entry.get("body", ""),
            "ip": log_entry["ip"],
            "query": log_entry.get("query", {}),
            "user_agent": log_entry.get("user_agent", ""),
            "headers": log_entry.get("headers", {}),
        }
        result = asyncio.run(feed.process_request(request_for_feed))

        # Attack must be flagged
        assert result is not None, "Feed returned no result for attack"
        assert result.verdict == "FLAGGED", f"Attack not flagged: {result.verdict}"

        # Tarpit file must exist with this IP
        tarpit_file = lab_server["tarpit_file"]
        assert os.path.exists(tarpit_file), "Tarpit file was not written"
        with open(tarpit_file) as f:
            tarpit_state = json.loads(f.read())
        assert log_entry["ip"] in tarpit_state, f"IP {log_entry['ip']} not tarpitted"
        assert tarpit_state[log_entry["ip"]]["delay"] >= 1.0

        # ── Phase 4: tarpit delay must be REAL — second request is slow ──
        start = time.time()
        req.get(f"{base}/health", timeout=15)
        elapsed = time.time() - start
        assert elapsed >= 0.5, f"Tarpit delay not applied: {elapsed:.3f}s (expected >= 0.5s)"

    def test_scanner_ua_detected(self, lab_server, blue_stack):
        """Scanner user-agent should trigger pattern detection."""
        from medusa.core.blue.tui.feed import _detect_obvious_attack

        req_data = {
            "method": "GET", "path": "/auth/login", "body": "",
            "user_agent": "sqlmap/1.7.10#stable (https://sqlmap.org)",
            "query": {}, "headers": {}, "ip": "10.9.9.9",
        }
        check = _detect_obvious_attack(req_data)
        assert check["score"] >= 5, f"Scanner UA not detected: score {check['score']}"
        assert any("Scanner" in p[0] for p in check["patterns"])

    def test_clean_traffic_not_flagged(self, lab_server, blue_stack):
        """Clean requests after baseline must NOT be flagged or tarpitted."""
        feed = blue_stack
        for i in range(3):
            clean = {
                "method": "GET", "path": "/health",
                "ip": f"10.1.1.{i+1}", "body": "", "query": {},
                "user_agent": "test-client", "headers": {},
            }
            result = asyncio.run(feed.process_request(clean))
            # Clean request may be None (normal) or a benign AIAnalysisResult
            if result is not None:
                assert result.verdict != "FLAGGED", f"Clean request flagged: {result.verdict}"

    def test_kg_records_attack(self, lab_server, blue_stack, isolated_tarpit_path):
        """The knowledge graph must record the attack for cross-session tracking."""
        import requests as req

        from medusa.core.blue.knowledge_graph import get_kg

        feed = blue_stack
        base = lab_server["base_url"]
        kg = get_kg()
        kg.clear()

        # Baseline
        for i in range(3):
            asyncio.run(feed.process_request({
                "method": "GET", "path": "/health", "ip": f"10.2.2.{i+1}",
                "body": "", "query": {}, "user_agent": "test", "headers": {},
            }))

        # Attack
        resp = req.post(
            f"{base}/auth/login",
            json={"username": "admin' OR '1'='1", "password": "x"},
            timeout=5,
        )
        assert resp.status_code in (200, 401, 403)

        log_entry = _read_last_log_entry(lab_server["traffic_log"])
        asyncio.run(feed.process_request({
            "method": log_entry["method"], "path": log_entry["path"],
            "body": log_entry.get("body", ""), "ip": log_entry["ip"],
            "query": log_entry.get("query", {}),
            "user_agent": log_entry.get("user_agent", ""),
            "headers": log_entry.get("headers", {}),
        }))

        # KG should have the attacker
        history = kg.get_attacker_history(log_entry["ip"])
        assert history.get("total_flags", 0) >= 1, f"No attack recorded for {log_entry['ip']}"
        kg.clear()
