"""End-to-end integration tests for the blue team pipeline."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestBlueTeamPipeline:
    """Verify the full blue team pipeline works end-to-end."""

    @pytest.fixture
    def temp_traffic_log(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({
                "method": "POST", "path": "/auth/login", "ip": "192.168.1.100",
                "body": "admin' OR '1'='1", "user_agent": "sqlmap/1.7",
                "query": {}, "headers": {"Content-Type": "application/json"},
            }) + "\n")
            f.write(json.dumps({
                "method": "GET", "path": "/", "ip": "127.0.0.1",
                "body": "", "user_agent": "Mozilla/5.0",
                "query": {}, "headers": {},
            }) + "\n")
            path = f.name
        yield path
        os.unlink(path)

    def test_pattern_detector_finds_sqli(self, temp_traffic_log):
        """Verify attack pattern detector catches SQLi in traffic log."""
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        with open(temp_traffic_log) as f:
            for line in f:
                req = json.loads(line)
                result = _detect_obvious_attack({
                    "body": req.get("body", ""),
                    "user_agent": req.get("user_agent", ""),
                    "path": req.get("path", "/"),
                    "query": str(req.get("query", {})),
                    "headers": str(req.get("headers", {})),
                })
                if "sqlmap" in req.get("user_agent", ""):
                    assert result["score"] >= 4  # Scanner UA detected
                if "' OR '1'='1" in req.get("body", ""):
                    assert result["score"] >= 6  # SQLi detected

    def test_kg_records_attack_chain(self):
        """Verify knowledge graph records full attack → defense chain."""
        from medusa.core.blue.knowledge_graph import BlueKnowledgeGraph
        kg = BlueKnowledgeGraph()
        kg.add_attack("10.0.0.1", "/login", "SQL Injection", 8, "payload")
        kg.add_defense("10.0.0.1", "tarpit", "5.8s delay")
        kg.add_attack("10.0.0.1", "/search", "SQL Injection", 9, "UNION SELECT")
        kg.add_defense("10.0.0.1", "block", "Repeat offender")
        hist = kg.get_attacker_history("10.0.0.1")
        assert hist["total_flags"] >= 2
        assert len(hist["attacks"]) == 2
        assert len(hist["defenses"]) == 2
        # Second defense should be block (escalation)
        assert any("block" in str(d).lower() for d in hist["defenses"])

    def test_config_validation_roundtrip(self):
        """Verify config survives model_dump → model_validate roundtrip."""
        from medusa.core.config_models import BlueConfig, RedConfig
        blue = BlueConfig()
        blue2 = BlueConfig(**blue.model_dump())
        assert blue2.scorer.critical_threshold == blue.scorer.critical_threshold
        assert blue2.deception.tarpit_delay_seconds == blue.deception.tarpit_delay_seconds

        red = RedConfig()
        red2 = RedConfig(**red.model_dump())
        assert red2.cost_hard_cap_usd == red.cost_hard_cap_usd

    def test_errors_module_wired(self):
        """Verify structured error types are importable and functional."""
        from medusa.core.blue.errors import (
            ErrorSeverity,
            FirewallError,
            err,
            ok,
        )
        e = FirewallError("test", severity=ErrorSeverity.CRITICAL)
        r = err(e)
        assert r["status"] == "error"
        assert r["error"]["severity"] == "critical"
        assert r["error"]["source"] == "firewall"

        r2 = ok("success")
        assert r2["status"] == "ok"

    def test_fugu_importable(self):
        """Verify Fugu is no longer dead code — importable from main."""
        from medusa.fugu import run_fugu
        assert run_fugu is not None
        from medusa.fugu_chain import ChainTracker
        assert ChainTracker is not None

    def test_deception_engine_wired(self):
        """Verify deception engine loads and uses structured errors."""
        from medusa.core.blue.defense.deception_engine import DeceptionEngine
        engine = DeceptionEngine()
        r = engine.decide_response("attacker-1", {"ip": "1.2.3.4"}, 9)
        assert r["status"] == "ok"
        # Score 7 → tarpit
        r2 = engine.decide_response("attacker-2", {"ip": "2.3.4.5"}, 7)
        assert r2["status"] == "ok"
        # Score 5 → honeypot
        r3 = engine.decide_response("attacker-3", {"ip": "3.4.5.6"}, 5)
        assert r3["status"] == "ok"
        # Score 3 → observe
        r4 = engine.decide_response("attacker-4", {"ip": "4.5.6.7"}, 3)
        assert r4["status"] == "ok"

    def test_tarpit_uses_structured_errors(self):
        """Verify tarpit returns ok()/err() results."""
        from medusa.core.blue.defense.tarpit import Tarpit
        t = Tarpit()
        result = t.engage("10.0.0.1", delay=0.1)
        assert result["status"] == "ok"

    def test_proxy_importable(self):
        """Verify proxy server is importable and configurable."""
        from medusa.core.blue.proxy import ProxyServer, start_proxy
        assert ProxyServer is not None
        assert start_proxy is not None

    def test_subagent_manager_wired(self):
        """Verify subagent manager deploy + analyze flow imports."""
        from medusa.core.blue.subagent_manager import EndpointSubagent, SubagentManager
        assert SubagentManager is not None
        # EndpointSubagent has all fields
        sa = EndpointSubagent(
            agent_id="test-01", endpoint={"method": "GET", "path": "/test"},
            rank=1, risk_score=5, handler_code="def test(): pass",
            honeypot_code="@app.route('/trap')\ndef trap(): return 'ok'",
            patch_code="def test(): return 'fixed'",
        )
        assert sa.honeypot_code
        assert sa.patch_code

    def test_config_models_validated_at_startup(self):
        """Verify config validation catches bad values."""
        import pytest

        # BlueConfig with invalid scorer weight
        from pydantic import ValidationError

        from medusa.core.config_models import BlueConfig, RedConfig
        with pytest.raises(ValidationError):
            BlueConfig(scorer={"critical_threshold": 999})  # Out of range
        # RedConfig with negative cost
        with pytest.raises(ValidationError):
            RedConfig(cost_hard_cap_usd=-5.0)
