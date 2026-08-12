"""Tests for blue team core — AI engine, knowledge graph, normalizer, attack patterns."""
import pytest, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestAttackPatterns:
    """Verify all 18 attack pattern detectors work correctly."""

    def test_sqli_detection(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": "admin' OR '1'='1", "user_agent": "", "path": "/login",
            "query": "", "headers": "",
        })
        assert result["score"] >= 6
        assert any("SQL" in p[0] for p in result["patterns"])

    def test_xss_detection(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": "<script>alert(1)</script>", "user_agent": "",
            "path": "/search", "query": "", "headers": "",
        })
        assert result["score"] >= 6

    def test_path_traversal_detection(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": "../../../etc/passwd", "user_agent": "", "path": "/download",
            "query": "", "headers": "",
        })
        assert result["score"] >= 5

    def test_ssrf_detection(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": "url=http://169.254.169.254/latest/meta-data", "user_agent": "",
            "path": "/webhook", "query": "", "headers": "",
        })
        assert result["score"] >= 6

    def test_scanner_ua_detection(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": "", "user_agent": "sqlmap/1.7.10#stable", "path": "/",
            "query": "", "headers": "",
        })
        assert result["score"] >= 4

    def test_mass_assignment_detection(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": '{"username":"test","role":"admin"}', "user_agent": "",
            "path": "/register", "query": "", "headers": "",
        })
        assert result["score"] >= 5

    def test_auth_bypass_header(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": "", "user_agent": "", "path": "/admin",
            "query": "", "headers": "{'X-Admin': 'true'}",
        })
        assert result["score"] >= 5

    def test_clean_request_passes(self):
        from medusa.core.blue.tui.feed import _detect_obvious_attack
        result = _detect_obvious_attack({
            "body": '{"username":"joe","password":"safe123"}', "user_agent": "Mozilla/5.0",
            "path": "/login", "query": "", "headers": "{'Content-Type': 'application/json'}",
        })
        assert result["score"] <= 2

    def test_all_18_patterns_compile(self):
        import re
        from medusa.core.blue.tui.feed import _ATTACK_PATTERNS
        assert len(_ATTACK_PATTERNS) == 18
        for name, pattern, weight in _ATTACK_PATTERNS:
            re.compile(pattern)


class TestKnowledgeGraph:
    """Verify knowledge graph CRUD operations."""

    def test_add_attacker_and_attack(self):
        from medusa.core.blue.knowledge_graph import BlueKnowledgeGraph
        kg = BlueKnowledgeGraph()
        kg.add_attack("10.0.0.1", "/login", "SQL Injection", 8, "admin' OR 1=1")
        hist = kg.get_attacker_history("10.0.0.1")
        assert hist["total_flags"] >= 1
        assert len(hist["attacks"]) >= 1
        assert hist["attacks"][0]["attack_type"] == "SQL Injection"

    def test_add_defense(self):
        from medusa.core.blue.knowledge_graph import BlueKnowledgeGraph
        kg = BlueKnowledgeGraph()
        kg.add_attack("10.0.0.2", "/api/users", "IDOR", 6, "")
        kg.add_defense("10.0.0.2", "tarpit", "5.8s delay")
        hist = kg.get_attacker_history("10.0.0.2")
        assert len(hist["defenses"]) >= 1

    def test_attacker_flags_increment(self):
        from medusa.core.blue.knowledge_graph import BlueKnowledgeGraph
        kg = BlueKnowledgeGraph()
        kg.add_attack("10.0.0.3", "/login", "SQLi", 6, "")
        kg.add_attack("10.0.0.3", "/search", "SQLi", 7, "")
        hist = kg.get_attacker_history("10.0.0.3")
        assert hist["total_flags"] >= 2

    def test_bridge_from_red_team(self):
        from medusa.core.blue.knowledge_graph import BlueKnowledgeGraph
        kg = BlueKnowledgeGraph()
        imported = kg.bridge_from_red_team()
        assert isinstance(imported, int)


class TestSmartNormalizer:
    """Verify traffic normalizer pattern hashing and baseline learning."""

    def test_hash_pattern_normalizes_ids(self):
        from medusa.core.blue.traffic.normalizer import SmartNormalizer
        n = SmartNormalizer()
        h1 = n._hash_pattern({"method": "GET", "path": "/api/users/42", "query": {}, "body": ""})
        h2 = n._hash_pattern({"method": "GET", "path": "/api/users/99", "query": {}, "body": ""})
        assert h1 == h2  # Same pattern, different IDs

    def test_hash_pattern_differentiates_methods(self):
        from medusa.core.blue.traffic.normalizer import SmartNormalizer
        n = SmartNormalizer()
        h1 = n._hash_pattern({"method": "GET", "path": "/login", "query": {}, "body": ""})
        h2 = n._hash_pattern({"method": "POST", "path": "/login", "query": {}, "body": ""})
        assert h1 != h2

    def test_is_known_normal(self):
        from medusa.core.blue.traffic.normalizer import SmartNormalizer
        n = SmartNormalizer()
        req = {"method": "GET", "path": "/", "query": {}, "body": "", "ip": "1.2.3.4"}
        n._learn_from_request(req)
        assert n.is_known_normal(req)

    def test_add_to_baseline(self):
        from medusa.core.blue.traffic.normalizer import SmartNormalizer
        n = SmartNormalizer()
        req = {"method": "POST", "path": "/api/data", "query": {"page": "1"}, "body": "x=1", "ip": "1.2.3.4"}
        n.add_to_baseline(req)
        assert n.is_known_normal(req)
