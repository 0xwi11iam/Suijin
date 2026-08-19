"""Wave 2 batch 1: dead-end detector, payload escalation, confidence, profiles."""

from suijin.modules.agent.lib.profiles import PROFILES, get_profile, profile_directive
from suijin.modules.agent.lib.supervisor import (
    _confidence_from_decision,
    _detect_dead_end,
    _detect_payload_class_escalation,
)


def _step(tool, ok=True, args=""):
    return {"tool_name": tool, "success": ok, "tool_args": args}


class TestDeadEnd:
    def test_same_tool_failing_variably(self):
        trace = [_step("http_request", False, "url=1"), _step("http_request", False, "url=2"), _step("http_request", False, "url=3")]
        out = _detect_dead_end(trace)
        assert out and "DEAD END" in out and "strategy CLASS" in out

    def test_succeeding_repeat_not_flagged(self):
        trace = [_step("nmap_scan", True, "1"), _step("nmap_scan", True, "2"), _step("nmap_scan", True, "3")]
        assert _detect_dead_end(trace) is None

    def test_mixed_tools_not_flagged(self):
        trace = [_step("a", False), _step("b", False), _step("a", False)]
        assert _detect_dead_end(trace) is None


class TestPayloadEscalation:
    def test_reflected_grind_escalates(self):
        trace = [_step("http_request", False, "body=' OR 1=1--"), _step("http_request", False, "body=<script>x</script>"),
                 _step("http_request", False, "body=../../etc/passwd"), _step("http_request", False, "body=' OR 'a'='a")]
        out = _detect_payload_class_escalation(trace)
        assert out and "PAYLOAD CLASS ESCALATION" in out and "BLIND" in out

    def test_mixed_failure_not_injection_args(self):
        trace = [_step("http_request", False, "timeout"), _step("http_request", False, "dns fail"),
                 _step("http_request", False, "conn refused"), _step("http_request", False, "tls")]
        assert _detect_payload_class_escalation(trace) is None or "escalate" not in (_detect_payload_class_escalation(trace) or "")


class TestConfidence:
    def test_normalization(self):
        assert _confidence_from_decision({"confidence": "Verified"}) == "verified"
        assert _confidence_from_decision({"confidence": "likely"}) == "probable"
        assert _confidence_from_decision({"confidence": "maybe"}) == "suspected"
        assert _confidence_from_decision({}) == "probable"  # unclaimed default


class TestProfiles:
    def test_all_profiles_shape(self):
        for name, p in PROFILES.items():
            assert p["directive"] and "OPERATING PROFILE" in p["directive"]
            assert isinstance(p["pacing_delay_s"], (int, float))

    def test_selection(self):
        assert get_profile({"adversary_profile": "stealth_apt"})["pacing_delay_s"] == 2.0
        assert get_profile({}) is None
        assert profile_directive({}) == ""
        assert "stealth" in profile_directive({"adversary_profile": "stealth_apt"})
