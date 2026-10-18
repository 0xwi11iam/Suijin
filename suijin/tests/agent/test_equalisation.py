"""Tests for the CyberStrike/ARTEX equalisation modules.

Five systems built in one round:
- Observation engine (deterministic IDOR/BFLA/mass-assignment)
- Vuln-class sub-agent profiles
- Layered loop detection
- Completion scoreboard
- WSTG skill generator
"""

from __future__ import annotations

from suijin.modules.agent.lib.completion_scoreboard import render_scoreboard, scoreboard
from suijin.modules.agent.lib.loop_detection import (
    ProgressSignals,
    RepeatDetector,
    is_progress,
)
from suijin.modules.agent.lib.observation import (
    detect_all,
    detect_bfla,
    detect_idor,
    record,
    reset,
    status,
)
from suijin.modules.agent.lib.vuln_agents import get_profile, list_classes


class TestObservationEngine:
    def setup_method(self):
        reset()

    def test_idor_detected_from_different_statuses(self):
        """user_a gets 200, user_b gets 403 on the same object → IDOR."""
        record("/api/user/123", "GET", "user_a", {}, 200, 500, iteration=1)
        record("/api/user/123", "GET", "user_b", {}, 403, 100, iteration=2)
        findings = detect_idor()
        assert any(f.kind == "idor" and f.severity == "high" for f in findings)

    def test_idor_detected_from_different_sizes(self):
        """Both 200 but user_a sees 1000B and user_b sees 200B → IDOR."""
        record("/api/data", "GET", "admin", {}, 200, 1000, iteration=1)
        record("/api/data", "GET", "user", {}, 200, 200, iteration=2)
        findings = detect_idor()
        assert any(f.kind == "idor" and f.severity == "medium" for f in findings)

    def test_no_idor_when_identical(self):
        record("/api/data", "GET", "user_a", {}, 200, 500, iteration=1)
        record("/api/data", "GET", "user_b", {}, 200, 500, iteration=2)
        assert detect_idor() == []

    def test_bfla_detected(self):
        """Only admin reaches the endpoint → BFLA."""
        record("/admin/settings", "GET", "admin", {}, 200, 800, iteration=1)
        record("/admin/settings", "GET", "user_a", {}, 403, 100, iteration=2)
        record("/admin/settings", "GET", "user_b", {}, 403, 100, iteration=3)
        findings = detect_bfla()
        assert any(f.kind == "bfla" for f in findings)

    def test_endpoint_normalization(self):
        """IDs are normalized so /api/user/1 and /api/user/2 are the same endpoint."""
        record("/api/user/12345", "GET", "user_a", {}, 200, 500, iteration=1)
        record("/api/user/67890", "GET", "user_b", {}, 403, 100, iteration=2)
        findings = detect_idor()
        assert any(f.kind == "idor" for f in findings)

    def test_empty_returns_empty(self):
        assert detect_all() == []
        assert status() is not None


class TestVulnAgentProfiles:
    def test_all_classes_present(self):
        classes = list_classes()
        assert len(classes) == 9
        for c in (
            "idor",
            "injection",
            "ssrf",
            "authn",
            "authz",
            "mass_assignment",
            "business_logic",
            "file_upload",
            "info_disclosure",
        ):
            assert c in classes

    def test_profile_has_required_fields(self):
        for cls in list_classes():
            p = get_profile(cls)
            assert p is not None
            assert "description" in p
            assert "tools" in p
            assert "doctrine" in p

    def test_alias_resolution(self):
        assert get_profile("sqli") == get_profile("injection")
        assert get_profile("bac") == get_profile("authz")
        assert get_profile("jwt") == get_profile("authn")

    def test_unknown_returns_none(self):
        assert get_profile("definitely-not-real") is None


class TestLoopDetection:
    def test_repeat_detector_nudges_at_limit(self):
        det = RepeatDetector(limit=3)
        for _ in range(2):
            assert det.feed("http_request", {"url": "https://t/a"}).level == "ok"
        v = det.feed("http_request", {"url": "https://t/a"})
        assert v.level == "nudge"
        assert "change" in v.message.lower()

    def test_repeat_detector_aborts_after_nudge(self):
        det = RepeatDetector(limit=3)
        for _ in range(3):
            det.feed("http_request", {"url": "https://t/a"})
        v = det.feed("http_request", {"url": "https://t/a"})
        assert v.level == "abort"

    def test_different_args_do_not_trip(self):
        det = RepeatDetector(limit=3)
        det.feed("http_request", {"url": "https://t/1"})
        det.feed("http_request", {"url": "https://t/2"})
        v = det.feed("http_request", {"url": "https://t/3"})
        assert v.level == "ok"

    def test_gate_refusal_is_progress(self):
        """A completion refusal is dialogue, not stagnation."""
        signals = ProgressSignals(completion_refused=True)
        assert is_progress(signals) is True

    def test_successful_tool_is_progress(self):
        assert is_progress(ProgressSignals(tool_succeeded=True)) is True

    def test_nothing_is_not_progress(self):
        assert is_progress(ProgressSignals()) is False


class TestCompletionScoreboard:
    def test_no_chain_or_foothold_blockers(self):
        sb = scoreboard({})
        # empty state: coverage cells exist as blockers (correct — nothing
        # tested yet), but chain and foothold blockers should be ABSENT
        assert not any("chain" in b.lower() for b in sb["blockers"])
        assert not any("depth" in b.lower() for b in sb["blockers"])

    def test_chain_pending_blocks(self):
        sb = scoreboard({"_chain_pending": {"finding": "JWT bypass", "explored": False}})
        assert sb["can_complete"] is False
        assert any("chain" in b.lower() for b in sb["blockers"])

    def test_untested_footholds_block(self):
        sb = scoreboard({"_footholds": [{"capability": "test", "status": "open", "unlock_targets": []}]})
        assert sb["can_complete"] is False
        assert any("depth" in b.lower() for b in sb["blockers"])

    def test_scoreboard_renders(self):
        text = render_scoreboard({})
        assert "COMPLETION STATUS" in text
