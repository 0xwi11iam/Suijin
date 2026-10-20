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
        for vc in self.cov.CLASSES:
            if vc in ("info", "redirect"):
                continue
            self.cov.mark(BASE, vc, "not_applicable", evidence="ev", request_sent="GET /")
        assert self.cov.completion_blocked([BASE]) is None

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
