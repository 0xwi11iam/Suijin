"""Wave 3: B11-B18 — memory, delta/drift, advisor, evidence, dedup, paths, freshness."""

from unittest import mock

from suijin.modules.agent.lib import memory
from suijin.modules.knowledge.lib import advisor
from suijin.modules.tools.lib import evidence


class TestMemory:
    def test_record_recall_cycle(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        memory.record_engagement("t.example", "own the web", {"completion_reason": "complete"})
        memory.note("t.example", "operator prefers quiet scans")
        out = memory.recall("t.example")
        assert "1 prior engagement" in out and "own the web" in out and "quiet scans" in out

    def test_delta_detects_change(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        assert not memory.record_fingerprint("t.example", {"server": "nginx", "port": "443"})
        assert memory.record_fingerprint("t.example", {"server": "apache", "port": "443"})
        d = memory.delta("t.example", {"server": "iis", "port": "8443"})
        assert "TARGET DELTA (2 change(s))" in d


class TestAdvisor:
    def test_cve_mapping(self):
        out = advisor.advise_tools("CVE-2021-44228 log4j")
        assert "searchsploit_find" in out

    def test_sqli_mapping(self):
        assert "sqli_polyglots" in advisor.advise_tools("sql injection in login")

    def test_unknown_falls_back(self):
        assert "no curated mapping" in advisor.advise_tools("quantum overflow")

    def test_kb_freshness(self):
        with mock.patch(
            "suijin.modules.knowledge.lib.kb.kb_status", return_value={"docs": 100, "sources": 5, "age_days": 90}
        ):
            assert "STALE" in advisor.kb_freshness()
        with mock.patch(
            "suijin.modules.knowledge.lib.kb.kb_status", return_value={"docs": 100, "sources": 5, "age_days": 3}
        ):
            assert "fresh" in advisor.kb_freshness()


class TestEvidence:
    def test_chain_and_tamper(self, tmp_path, monkeypatch):
        import json

        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        evidence.capture({"type": "sqli", "target": "t"}, "proof: ' OR 1=1")
        evidence.capture({"type": "xss", "target": "t"}, "proof: <script>")
        ok, _ = evidence.verify_chain()
        assert ok
        # tamper with the first record's evidence
        chain_path = tmp_path / "outputs" / "evidence" / "chain.json"
        chain = json.loads(chain_path.read_text())
        chain[0]["evidence_text"] = "fabricated"
        chain_path.write_text(json.dumps(chain))
        ok2, problems = evidence.verify_chain()
        assert not ok2 and any("mismatch" in p for p in problems)

    def test_dedup_collapses_same_root_cause(self):
        findings = [
            {"type": "sqli", "target": "t", "evidence": "union select", "path": "/a"},
            {"type": "sqli", "target": "t", "evidence": "union select", "path": "/b"},
            {"type": "xss", "target": "t", "evidence": "script tag", "path": "/c"},
        ]
        out = evidence.dedup(findings)
        assert len(out) == 2
        sqli = next(f for f in out if f["type"] == "sqli")
        assert sqli["occurrences"] == ["/a", "/b"]

    def test_path_scoring(self):
        findings = [
            {"type": "sqli", "target": "t", "confidence": "verified", "phase": "exploitation"},
            {"type": "rce", "target": "t", "confidence": "probable", "phase": "post"},
        ]
        out = evidence.score_paths(findings)
        assert "attack paths" in out and "0.54" in out  # 0.9 * 0.6
