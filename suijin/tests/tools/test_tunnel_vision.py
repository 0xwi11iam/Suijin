"""Tunnel-vision signal — the harness carries the diversity push on the
result the model MUST read next (prompting alone didn't break the
one-tool-all-day pattern).
"""


from suijin.server.agent.lib.nodes.execute_tool_node import _tunnel_vision_notice


def _state(tool, n=3):
    return {"execution_trace": [{"tool": tool} for _ in range(n)], "original_objective": "10.0.0.9"}


class TestStreak:
    def test_fourth_consecutive_call_fires(self):
        msg = _tunnel_vision_notice(_state("http_request", 3), "http_request", {"url": "http://10.0.0.9/a"}, "Status: 200 OK")
        assert msg.startswith("[TUNNEL VISION")
        assert "http_request four+ calls" in msg

    def test_third_call_still_quiet(self):
        assert _tunnel_vision_notice(_state("http_request", 2), "http_request", {"url": "http://10.0.0.9/a"}, "Status: 200") == ""

    def test_varied_tools_stay_quiet(self):
        st = {"execution_trace": [{"tool": "read_file"}, {"tool": "http_request"}, {"tool": "read_file"}]}
        assert _tunnel_vision_notice(st, "http_request", {}, "Status: 200") == ""

    def test_progress_exempts(self):
        msg = _tunnel_vision_notice(
            _state("catalog_exploit", 3), "catalog_exploit", {}, "EXP-004 CONFIRMED — HIGH CVSS 8.1"
        )
        assert msg == ""  # a fresh finding is not tunnel vision

    def test_alternative_tools_named(self):
        msg = _tunnel_vision_notice(_state("http_request", 3), "http_request", {"url": "http://10.0.0.9/a"}, "Status: 200")
        assert "alternative tools:" in msg and "curl" in msg

    def test_never_raises_on_odd_state(self):
        assert _tunnel_vision_notice({}, "http_request", None, "") == ""
        assert _tunnel_vision_notice({"execution_trace": ["junk"]}, "x", {}, "y") == ""
