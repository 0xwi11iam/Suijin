"""Weaponization engine tests — the go-bigger round (2026-10-20).

The behavior change these tests pin:
- coverage classes are weighted; untested lists sort by weight DESC
- trivia cells (info, redirect) never block completion
- satiation fires at 4 trivia + 0 real findings, pressure-only
- attack scenarios need 2+ findings with at least one weight ≥ 5
- the depth directive carries the PROMOTE order in recon phase
"""

import pytest

from suijin.modules.agent.lib.weaponization import (
    BLOCKING_MIN_WEIGHT,
    CLASS_WEIGHTS,
    SATIATION_TRIVIA,
    attack_scenarios,
    class_weight,
    directive,
    finding_weight,
    satiation,
    weapons_block,
)

BASE = "https://test.example"

TRIVIA_FINDINGS = [
    {"title": "INFO CVSS 3.1 : missing security header"},
    {"title": "LOW CVSS 3.7 : verbose error page"},
    {"title": "INFO : source map exposes comments"},
    {"title": "LOW : server version banner"},
]
REAL_FINDING = {"title": "HIGH CVSS 8.2 : IDOR on /api/v1/users/{id}"}


class TestClassWeights:
    def test_every_coverage_class_weighted(self):
        from suijin.modules.tools.lib.coverage import CLASSES

        for vc in CLASSES:
            assert vc in CLASS_WEIGHTS, f"{vc} has no weight"
            assert 1 <= CLASS_WEIGHTS[vc] <= 15

    def test_crown_jewels_outweigh_trivia(self):
        assert class_weight("authn") > class_weight("info") * 5
        assert class_weight("sqli") > class_weight("redirect") * 4

    def test_unknown_class_is_medium(self):
        assert class_weight("warp_drive") == 5

    def test_trivia_below_blocking_threshold(self):
        assert class_weight("info") < BLOCKING_MIN_WEIGHT
        assert class_weight("redirect") < BLOCKING_MIN_WEIGHT
        assert class_weight("xss") >= BLOCKING_MIN_WEIGHT


class TestFindingWeight:
    def test_title_prefix_parsed(self):
        assert finding_weight({"title": "CRITICAL CVSS 9.8 : RCE"}) == 15
        assert finding_weight({"title": "HIGH CVSS 7.2 : reflected"}) == 10
        assert finding_weight({"title": "INFO : header"}) == 1

    def test_severity_field_fallback(self):
        assert finding_weight({"title": "x", "system_severity": "critical"}) == 15
        assert finding_weight({"title": "x", "severity": "high"}) == 10

    def test_class_fallback_then_default(self):
        assert finding_weight({"title": "x", "class": "sqli"}) == 10
        assert finding_weight({"title": "x"}) == 1
        assert finding_weight("not-a-dict") == 1


class TestSatiation:
    def test_fires_at_four_trivia_zero_real(self):
        s = satiation({"findings": TRIVIA_FINDINGS})
        assert s["saturated"] is True
        assert s["trivia"] == SATIATION_TRIVIA and s["real"] == 0

    def test_three_trivia_not_saturated(self):
        assert satiation({"findings": TRIVIA_FINDINGS[:3]})["saturated"] is False

    def test_one_real_desaturates(self):
        s = satiation({"findings": TRIVIA_FINDINGS + [REAL_FINDING]})
        assert s["saturated"] is False and s["real"] == 1

    def test_no_findings_not_saturated(self):
        assert satiation({"findings": []})["saturated"] is False


class TestDirective:
    def test_directive_empty_when_not_saturated(self):
        assert directive({"findings": TRIVIA_FINDINGS[:2]}) == ""

    def test_directive_lists_weapons(self):
        d = directive({"findings": TRIVIA_FINDINGS})
        assert "auth_forge" in d and "cred_matrix" in d and "race_test" in d

    def test_directive_promotes_from_recon_only(self):
        recon = directive({"findings": TRIVIA_FINDINGS, "current_phase": "recon"})
        expl = directive({"findings": TRIVIA_FINDINGS, "current_phase": "exploitation"})
        assert "PROMOTE" in recon
        assert "PROMOTE" not in expl  # already there — no promotion order


class TestAttackScenarios:
    def test_needs_two_findings(self):
        assert attack_scenarios({"findings": [REAL_FINDING]}) == []

    def test_identity_x_api_pair_matches(self):
        scen = attack_scenarios(
            {
                "findings": [
                    {"title": "HIGH CVSS 7.1 : JWT alg-none bypass on /auth/token"},
                    {"title": "MEDIUM : internal API surface /internal/admin discovered"},
                ]
            }
        )
        assert scen and "replay" in scen[0].lower()

    def test_trivia_pair_yields_nothing(self):
        assert attack_scenarios({"findings": [{"title": "INFO : a"}, {"title": "LOW : b"}]}) == []

    def test_fallback_when_no_pattern_matches(self):
        scen = attack_scenarios({"findings": [REAL_FINDING, {"title": "HIGH CVSS 7.0 : weird novel bug"}]})
        assert scen and "NEXT" in scen[0]

    def test_capped_at_limit(self):
        founds = [{"title": f"HIGH CVSS 8.0 : jwt bypass {i}"} for i in range(4)] + [
            {"title": f"MEDIUM : internal api endpoint {i}"} for i in range(4)
        ]
        assert len(attack_scenarios({"findings": founds}, limit=3)) <= 3


class TestWeaponsBlock:
    def test_lists_depth_tools(self):
        w = weapons_block({})
        for tool in ("auth_forge", "cred_matrix", "race_test", "mass_assign", "graphql_attack"):
            assert tool in w


class TestChainCandidates:
    """B (2026-10-20): detected finding pairs are obligations."""

    PAIR_STATE = {
        "findings": [
            {"id": "EXP-001", "title": "HIGH CVSS 7.1 : JWT alg-none bypass on /auth/token"},
            {"id": "EXP-002", "title": "MEDIUM : internal API surface /internal/admin discovered"},
        ]
    }

    def test_pair_yields_candidate_with_clearing_classes(self):
        cands = chain_candidates(self.PAIR_STATE)
        assert len(cands) == 1
        assert set(cands[0]["classes"]) & {"authn", "authz", "idor"}

    def test_trivia_pair_no_candidate(self):
        assert chain_candidates({"findings": [{"title": "INFO : a"}, {"title": "LOW : b"}]}) == []

    def test_explicit_mark_clears(self):
        from suijin.modules.agent.lib.weaponization import unexplored_candidates

        cands = chain_candidates(self.PAIR_STATE)
        state = dict(self.PAIR_STATE)
        state["_chain_explored"] = {cands[0]["id"]: True}
        assert unexplored_candidates(state) == []

    def test_coverage_mark_clears(self, tmp_path, monkeypatch):

        monkeypatch.setenv("SUIJIN_WORKSPACE", str(tmp_path))
        from suijin.modules.tools.lib import coverage as cov

        store = tmp_path / "coverage.json"
        cov._store_path = lambda: store
        cov.reset()
        from suijin.modules.agent.lib.weaponization import unexplored_candidates

        # mark one of the candidate's classes tested → cleared
        # (the mark itself must clear the ≥50-char N/A justification bar)
        cov.mark(
            "https://x.test",
            "authn",
            "not_applicable",
            evidence="static public origin with no authentication flow reachable — verified by route map",
        )
        assert unexplored_candidates(self.PAIR_STATE) == []
        cov.reset()


class TestRedFlags:
    """A (2026-10-20): anti-gaming flags over the ledger shape."""

    @pytest.fixture(autouse=True)
    def _fresh_ledger(self, tmp_path):
        from suijin.modules.tools.lib import coverage as cov

        store = tmp_path / "coverage.json"
        cov._store_path = lambda: store
        cov.reset()
        self.cov = cov
        yield
        cov.reset()

    def test_contract_action_vocabulary_marks(self):
        """F-round (2026-10-20): the contract enum (tested_vulnerable
        etc. as ACTION) and the implementation verbs (mark/note/gate)
        disagreed — marking through the tool was impossible. Both
        vocabularies now land a real cell."""
        from suijin.modules.tools.lib.coverage import coverage_check

        r1 = coverage_check(
            action="not_applicable",
            asset="https://t.example",
            vuln_class="authn",
            evidence="static public origin, no authentication flow exists — verified by route enumeration",
        )
        assert "not_applicable" in r1 and "Error" not in r1
        r2 = coverage_check(
            action="mark",
            asset="https://t.example",
            vuln_class="authn",
            status="tested_vulnerable",
            evidence="auth bypass demonstrated with forged token",
        )
        assert "tested_vulnerable" in r2 and "Error" not in r2

    EV = "sent GET /x, got 403 with hardening present, repeated with encoded payload variant"

    def _mark6(self, evidence=EV):
        why = evidence if len(evidence) >= 50 else evidence + " " + "x" * 60
        for vc in ["authn", "authz", "idor", "sqli", "ssrf", "lfi"]:
            self.cov.mark("https://t.example", vc, "tested_not_vulnerable", evidence=why, request_sent="GET /")

    def test_copy_paste_flag(self):
        self._mark6()
        flags = red_flags({})
        assert any("copy_paste" in f for f in flags)

    def test_distinct_evidence_no_flag(self):
        for i, vc in enumerate(["authn", "authz", "idor"]):
            self.cov.mark(
                "https://t.example",
                vc,
                "tested_not_vulnerable",
                evidence=f"class-specific test {vc}: distinct request {i} " + "detail " * 8,
                request_sent="GET /",
            )
        assert red_flags({}) == []

    def test_na_flood_flag(self):
        why = "static JSON origin with no server-side rendering path — verified per class "
        for vc in ["authn", "authz", "idor", "sqli", "ssrf", "lfi", "xss", "upload", "xxe", "cmdi"]:
            self.cov.mark("https://t.example", vc, "not_applicable", evidence=why + vc)
        flags = red_flags({})
        assert any("not_applicable" in f for f in flags)


from suijin.modules.agent.lib.weaponization import chain_candidates, red_flags  # noqa: E402


class TestSeverityHonesty:
    """C (2026-10-20): the CODE sets severity from evidence presence."""

    def test_info_only_critical_capped(self):
        from suijin.modules.tools.lib.exploit_catalog import _honesty_check

        e = {
            "system_severity": "critical",
            "cvss": None,
            "marker": "server version banner",
            "description": "nginx version in error page",
            "poc": [],
        }
        _honesty_check(e)
        assert e["system_severity"] == "medium" and "severity_note" in e

    def test_cvss_stands_but_noted(self):
        from suijin.modules.tools.lib.exploit_catalog import _honesty_check

        e = {
            "system_severity": "high",
            "cvss": 9.1,
            "marker": "stack trace in response",
            "description": "verbose error message",
            "poc": [],
        }
        _honesty_check(e)
        assert e["cvss"] == 9.1 and "severity_note" in e

    def test_impact_evidence_untouched(self):
        from suijin.modules.tools.lib.exploit_catalog import _honesty_check

        e = {
            "system_severity": "critical",
            "cvss": None,
            "marker": "database dump of all users",
            "description": "sqli reads users table",
            "poc": [],
        }
        _honesty_check(e)
        assert e["system_severity"] == "critical" and "severity_note" not in e


class TestCoverageGateWeights:
    """The gate itself: trivia cells never block, ordering is weight DESC."""

    @pytest.fixture(autouse=True)
    def _fresh_ledger(self, tmp_path):
        # the established hermetic pattern (test_wave4_gates): pin the
        # store path to scratch — env swaps don't work, the workspace
        # path is resolved at import time
        from suijin.modules.tools.lib import coverage as cov

        store = tmp_path / "coverage.json"
        cov._store_path = lambda: store
        cov.reset()
        self.cov = cov
        yield
        cov.reset()

    def test_untested_sorted_by_weight_desc(self):
        ute = self.cov.untested([BASE], limit=999)
        ws = [u["weight"] for u in ute]
        assert all(ws[i] >= ws[i + 1] for i in range(len(ws) - 1))
        assert ute[0]["vuln_class"] in ("cmdi", "ssti")  # 15s lead

    def test_only_trivia_untested_gate_opens(self):
        # the agent tested every real class, skipped info + redirect
        # (A 2026-10-20: not_applicable on high-weight classes now
        # REQUIRES ≥50-char justification)
        why = "no server-side input path reaches a query context on this asset — static origin, verified"
        for vc in self.cov.CLASSES:
            if vc in ("info", "redirect"):
                continue
            self.cov.mark(BASE, vc, "not_applicable", evidence=why, request_sent="GET /")
        assert self.cov.completion_blocked([BASE]) is None

    def test_na_on_high_weight_requires_justification(self):
        # A: the free N/A gate-bypass is closed — bare N/A on a crown
        # jewel class errors
        r = self.cov.mark(BASE, "authn", "not_applicable")
        assert r.startswith("Error:") and "≥50" in r
        # trivia classes mark freely
        r2 = self.cov.mark(BASE, "info", "not_applicable")
        assert not r2.startswith("Error")

    def test_real_class_untested_gate_blocks(self):
        # only trivia tested — the crown jewels untouched
        for vc in ("info", "redirect"):
            self.cov.mark(BASE, vc, "not_applicable", evidence="ev", request_sent="GET /")
        blocked = self.cov.completion_blocked([BASE])
        assert blocked is not None and "COMPLETION REFUSED" in blocked
        # refusal counts only real cells (15 classes - 2 trivia)
        assert "high-impact" in blocked

    def test_refusal_counts_exclude_trivia(self):
        blocked = self.cov.completion_blocked([BASE])
        # all 15 untested but only 13 count (info+redirect excluded)
        assert "13 high-impact" in blocked
