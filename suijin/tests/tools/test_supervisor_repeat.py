"""Supervisor repeating-tool detector — signature fix (2026-10-29).

Live complaint: the nag fired 'same shape' every few iterations while the
agent hit DIFFERENT endpoints — the old check compared tool NAMES only.
The signature is now tool + canonical args: only genuinely identical
calls nag.
"""

from suijin.server.agent.lib.supervisor import _detect_repeating_tool


def _step(tool, args, success=True):
    return {"tool_name": tool, "tool_args": args, "success": success}


class TestSignatureNag:
    def test_identical_calls_still_nag(self):
        trace = [_step("http_request", {"url": "http://t/root"}) for _ in range(3)]
        out = _detect_repeating_tool(trace)
        assert out and "identical arguments" in out

    def test_different_endpoints_do_not_nag(self):
        trace = [
            _step("http_request", {"url": "http://t/api/v1/ping"}),
            _step("http_request", {"url": "http://t/api/v1/files"}),
            _step("http_request", {"url": "http://t/api/v1/files/1"}),
        ]
        assert _detect_repeating_tool(trace) is None

    def test_different_methods_do_not_nag(self):
        trace = [
            _step("http_request", {"method": "GET", "url": "http://t/x"}),
            _step("http_request", {"method": "POST", "url": "http://t/x"}),
            _step("http_request", {"method": "PUT", "url": "http://t/x"}),
        ]
        assert _detect_repeating_tool(trace) is None

    def test_different_bodies_do_not_nag(self):
        trace = [
            _step("http_request", {"method": "POST", "url": "http://t/x", "body": f'{{"n": {i}}}'})
            for i in range(3)
        ]
        assert _detect_repeating_tool(trace) is None

    def test_arg_key_order_irrelevant(self):
        """Same request, keys in different order = identical (canonical)."""
        trace = [
            _step("http_request", {"url": "http://t/x", "method": "GET"}),
            _step("http_request", {"method": "GET", "url": "http://t/x"}),
            _step("http_request", {"url": "http://t/x", "method": "GET"}),
        ]
        assert _detect_repeating_tool(trace) is not None

    def test_mixed_tools_do_not_nag(self):
        trace = [
            _step("read_file", {"file_path": "/a"}),
            _step("http_request", {"url": "http://t/x"}),
            _step("http_request", {"url": "http://t/x"}),
        ]
        assert _detect_repeating_tool(trace) is None

    def test_failed_calls_excluded(self):
        trace = [
            _step("http_request", {"url": "http://t/x"}, success=False),
            _step("http_request", {"url": "http://t/x"}, success=False),
            _step("http_request", {"url": "http://t/x"}, success=False),
        ]
        assert _detect_repeating_tool(trace) is None
