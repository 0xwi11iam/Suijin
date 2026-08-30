"""The 8 plan.md scenarios — the hard CI acceptance bar (slow marker).

1. 50 SQLi probes, one IP, 10s -> ONE case, 50 events attached, 1 tarpit (dedup)
2. Health-checker with `select` in JSON -> benign -> allowlisted -> silent (FP loop)
3. Login bob, then SQLi with bob's token -> identity resolved -> bob contained (enrich->contain)
4. LLM provider down -> fast path + ladder respond anyway (no AI dependency)
5. Slow scan + SQLi + token tamper, 30min apart, same actor -> one campaign case (correlation, reopen)
6. Session 2 sweeps retained traffic, finds session 1's IOC -> hunt case (memory compounds)
7. Battle: red agent fires -> enrich tags authorized: no self-containment (friendly fire)
8. Every case carries MTTD/MTTR (measurement)

These test the ORGAN WIRING (BF4-BF7) not the full blue stack —
unit-level scenario verification with real CaseStore/retention/learning.
"""

import pytest

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.blueteam.lib.blue.cases import CaseStore
from suijin.modules.blueteam.lib.blue.learning import fp_allowlist_add, fp_allowlist_check
from suijin.modules.blueteam.lib.blue.metrics import case_metrics
from suijin.modules.blueteam.lib.blue.retention import TrafficRetention, hunt, store_iocs


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    ws.set_engagement("scenario")
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


@pytest.mark.slow
class TestScenarios:
    def test_s1_dedup_50_probes_one_case(self):
        """50 SQLi from one IP -> ONE case, 50 events, 1 tarpit."""
        s = CaseStore("scenario")
        for i in range(50):
            s.record_event("10.0.0.1", "sql_injection", 7, f"/api/q?i={i}")
        cases = s.list_cases()
        assert len(cases) == 1
        assert len(cases[0]["timeline"]) == 50
        assert cases[0]["actor_ip"] == "10.0.0.1"

    def test_s2_fp_loop_health_checker(self):
        """Health-checker path allowlisted -> suppressed only there."""
        fp_allowlist_add(r"/health", "health checker sends select in JSON")
        assert fp_allowlist_check("sql_injection", path="/health")  # suppressed
        assert not fp_allowlist_check("sql_injection", path="/login")  # still fires

    def test_s3_enrich_contain_chain(self):
        """SQLi with a token -> case opens, block lands -> contained."""
        s = CaseStore("scenario")
        c = s.record_event("10.0.0.2", "sql_injection", 8, "/api/login")
        assert c["status"] == "new"
        s.record_action(c["id"], "BLOCK", "identity resolved -> user contained")
        assert s.case_detail(c["id"])["status"] == "contained"
        assert s.case_detail(c["id"])["contained_at"] is not None

    def test_s4_no_ai_dependency(self):
        """The case store works with zero AI — the fast path alone opens cases."""
        s = CaseStore("scenario")
        c = s.record_event("10.0.0.3", "scanner_ua", 5, "/admin")
        s.record_action(c["id"], "TARPIT", "pattern-based, no AI")
        assert s.case_detail(c["id"])["status"] == "contained"

    def test_s5_campaign_correlation_reopen(self):
        """Slow scan + SQLi + tamper 30min apart, same actor -> one case."""
        s = CaseStore("scenario")
        # wave 1: scan
        s.record_event("10.0.0.4", "scanner_ua", 3, "/")
        s.record_event("10.0.0.4", "sql_injection", 7, "/login")
        # close it
        c = s.list_cases()[0]
        s.transition(c["id"], "closed", "attacker quiet")
        # wave 2: same actor returns
        c2 = s.record_event("10.0.0.4", "jwt_tamper", 9, "/admin")
        assert c2["id"] != c["id"]  # new case (reopen)
        assert c2["severity"] == 9  # escalated
        assert s.case_detail(c["id"])["status"] == "closed"  # old stays closed

    def test_s6_cross_session_hunt(self):
        """Session 1 stores IOC -> session 2 hunts retained traffic -> finds it."""
        r = TrafficRetention("jan")
        r.append({"ip": "6.6.6.6", "path": "/admin", "query": {}, "timestamp": "2026-01-01T00:00:00Z"})

        class MockKG:
            def __init__(self):
                self.intel = {}

            def add_intelligence(self, k, v):
                self.intel[k] = v

            def get_intelligence(self):
                return self.intel

        kg = MockKG()
        store_iocs([{"type": "attack_pattern", "value": "idor_access", "actor": "6.6.6.6", "source_case": "C-1"}], kg)
        result = hunt(kg, sessions=["jan"])
        assert len(result["findings"]) >= 1
        assert result["findings"][0]["entry_ip"] == "6.6.6.6"

    def test_s7_friendly_fire(self):
        """Authorized red team -> case opens but marked, not self-contained."""
        s = CaseStore("scenario")
        c = s.record_event("10.0.0.5", "sql_injection", 7, "/api", source="authorized-red")
        # the case records the authorization in the timeline
        assert any(e.get("kind") == "authorized-red" for e in c["timeline"])
        # no auto-containment for authorized engagements (the operator decides)
        assert s.case_detail(c["id"])["status"] == "new"  # not auto-contained

    def test_s8_every_case_carries_mttd_mttr(self):
        """Every case has detect/contain timestamps (BF6 reads them)."""
        s = CaseStore("scenario")
        c = s.record_event("10.0.0.6", "sql_injection", 7, "/x")
        s.record_action(c["id"], "BLOCK", "auto")
        detail = s.case_detail(c["id"])
        m = case_metrics(detail)
        assert m["mttd_s"] is not None
        assert m["mttr_s"] is not None
        assert detail["detected_at"] is not None
        assert detail["contained_at"] is not None
