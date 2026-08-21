"""Tests for the red knowledge graph (suijin/intel/knowledge_graph.py) —
the agent's persistent per-target memory that check_knowledge/record_finding
route to. All file I/O monkeypatched into tmp_path.
"""

import json
import os

import pytest

from suijin.modules.redteam.lib.intel import knowledge_graph as kg

os.environ["SUIJIN_KG_BACKEND"] = "json"  # these tests pin JSON-backend semantics


@pytest.fixture(autouse=True)
def kg_file(tmp_path, monkeypatch):
    path = tmp_path / "knowledge_graph.json"
    monkeypatch.setattr(kg, "GRAPH_PATH", path)
    return path


class TestAddConstraint:
    def test_new_constraint_shape(self, kg_file):
        kg.add_constraint("10.0.0.1", "blocks", "' OR 1=1", evidence="403 from WAF")
        data = json.loads(kg_file.read_text())
        entry = data["10.0.0.1"]["blocks"][0]
        assert entry["rule"] == "' OR 1=1"
        assert entry["evidence"] == "403 from WAF"
        assert entry["confidence"] == 1.0
        assert entry["verified_at"] and entry["last_seen"]
        assert data["10.0.0.1"]["_updated"]

    def test_dedupe_merges_evidence_and_max_confidence(self, kg_file):
        kg.add_constraint("t", "blocks", "rule-a", evidence="first", confidence=0.5)
        kg.add_constraint("t", "blocks", "rule-a", evidence="second", confidence=0.9)
        data = json.loads(kg_file.read_text())
        entries = data["t"]["blocks"]
        assert len(entries) == 1  # deduplicated, not appended
        assert entries[0]["evidence"] == "second"  # updated
        assert entries[0]["confidence"] == 0.9  # max-merged

    def test_confidence_never_lowers(self, kg_file):
        kg.add_constraint("t", "waf", "cloudflare", confidence=1.0)
        kg.add_constraint("t", "waf", "cloudflare", confidence=0.3)
        assert json.loads(kg_file.read_text())["t"]["waf"][0]["confidence"] == 1.0

    def test_categories_are_independent(self, kg_file):
        kg.add_constraint("t", "blocks", "x")
        kg.add_constraint("t", "verified_cve", "CVE-2021-44228")
        cons = kg.get_constraints("t")
        assert [c["rule"] for c in cons["blocks"]] == ["x"]
        assert [c["rule"] for c in cons["verified_cve"]] == ["CVE-2021-44228"]


class TestCheckPayload:
    def test_blocked_substring_case_insensitive(self, kg_file):
        kg.add_constraint("t", "blocks", "' OR '1'='1")
        res = kg.check_payload("t", "admin' or '1'='1 --")
        assert res["blocked"] is True
        assert "Known block" in res["reason"]
        assert res["confidence"] == 1.0

    def test_not_blocked(self, kg_file):
        kg.add_constraint("t", "blocks", "' OR '1'='1")
        assert kg.check_payload("t", "<script>alert(1)</script>") == {"blocked": False}

    def test_nonstring_payload_safe(self, kg_file):
        # payload_lower only built for str; non-str must not raise
        res = kg.check_payload("t", None)
        assert res["blocked"] is False

    def test_empty_rule_ignored(self, kg_file):
        kg.add_constraint("t", "blocks", "")
        assert kg.check_payload("t", "anything") == {"blocked": False}

    def test_unknown_target(self, kg_file):
        assert kg.check_payload("nope", "' OR '1'='1") == {"blocked": False}


class TestQueries:
    def test_check_cve(self, kg_file):
        kg.add_constraint("t", "verified_cve", "CVE-2021-44228")
        assert kg.check_cve("t", "CVE-2021-44228") is True
        assert kg.check_cve("t", "CVE-2024-0001") is False

    def test_get_bypass_strategies(self, kg_file):
        kg.add_constraint("t", "bypass", "url-encode quotes")
        assert kg.get_bypass_strategies("t")[0]["rule"] == "url-encode quotes"

    def test_get_all_targets_excludes_metadata(self, kg_file):
        kg.add_constraint("a", "blocks", "x")
        kg.add_constraint("b", "waf", "y")
        assert sorted(kg.get_all_targets()) == ["a", "b"]

    def test_clear_target(self, kg_file):
        kg.add_constraint("t", "blocks", "x")
        kg.clear_target("t")
        assert kg.get_constraints("t") == {}
        assert "t" not in json.loads(kg_file.read_text())


class TestSummary:
    def test_no_knowledge(self):
        assert "No knowledge recorded" in kg.summary("fresh-target")

    def test_summary_lists_rules_with_partial_confidence(self, kg_file):
        kg.add_constraint("t", "blocks", "' OR 1=1", confidence=1.0)
        kg.add_constraint("t", "waf", "cloudflare", confidence=0.6)
        out = kg.summary("t")
        assert "Knowledge Graph • t" in out
        assert "' OR 1=1" in out
        assert "cloudflare" in out
        assert "[60%]" in out  # partial confidence shown
        assert "[100%]" not in out  # full confidence not annotated


class TestResilience:
    def test_corrupt_file_treated_as_empty(self, kg_file):
        kg_file.write_text("{not json!!")
        assert kg.get_constraints("anything") == {}

    def test_corrupt_file_recovers_on_write(self, kg_file):
        kg_file.write_text("{broken")
        kg.add_constraint("t", "blocks", "x")  # must not raise
        assert kg.check_payload("t", "x")["blocked"] is True

    def test_agent_routing_roundtrip(self, kg_file):
        # the actual agent surface: check_knowledge/record_finding
        from suijin.modules.tools.lib.intel import check_knowledge, record_finding

        record_finding("10.9.9.9", "blocks", "' OR '1'='1", evidence="403", config={})
        out = check_knowledge("10.9.9.9", payload="' or '1'='1 --", config={})
        assert "BLOCKED" in out and "Known block" in out
        out = check_knowledge("10.9.9.9", config={})
        assert "Knowledge Graph • 10.9.9.9" in out

    def test_record_finding_rejects_bad_type(self):
        from suijin.modules.tools.lib.intel import record_finding

        out = record_finding("t", "nonsense_type", "x", config={})
        assert "Invalid finding_type" in out
