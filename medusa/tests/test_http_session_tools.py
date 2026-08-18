"""Tests for session-aware HTTP behavior: SessionState (cookie/CSRF/auth
tracking), RateLimitTracker (429 + Retry-After + low-remaining throttle),
stealth UA rotation, and the http_request tool surface (mocked transport —
no network).
"""

from medusa.tools import http_tools
from medusa.tools.session_aware import (
    RateLimitTracker,
    SessionState,
    get_random_ua,
)


class TestSessionState:
    def test_set_cookie_extraction(self):
        s = SessionState("t")
        s.update_from_response({"Set-Cookie": "sid=abc123; Path=/; HttpOnly"}, "")
        assert s.cookies["sid"] == "abc123"
        assert s.get_cookie_string() == "sid=abc123"

    def test_csrf_token_extraction(self):
        s = SessionState("t")
        body = '<input type="hidden" name="csrf_token" value="tok-42">'
        s.update_from_response({}, body)
        assert s.csrf_tokens.get("form_token") == "tok-42"

    def test_is_authenticated(self):
        s = SessionState("t")
        assert s.is_authenticated() is False
        s.cookies["session"] = "x"
        assert s.is_authenticated() is True

    def test_touch_counts_requests(self):
        s = SessionState("t")
        s.touch()
        s.touch()
        assert s.request_count == 2 and s.last_request_at is not None


class TestRateLimitTracker:
    def test_429_sets_retry_after(self):
        t = RateLimitTracker()
        t.update("http://x.example/", 429, {"Retry-After": "30"})
        assert t.should_throttle("http://x.example/") > 2.0
        assert t.is_blocked("http://x.example/") is True

    def test_independent_after_window(self):
        import time as _time

        t = RateLimitTracker()
        t._domains["x.example"] = {"limit": 100, "remaining": 100,
                                   "reset_at": _time.time() - 1, "retry_after": 0}
        assert t.should_throttle("http://x.example/") == 0.0

    def test_low_remaining_throttles(self):
        t = RateLimitTracker()
        t.update("http://y.example/", 200,
                 {"X-RateLimit-Remaining": "2", "X-RateLimit-Limit": "100"})
        assert t.should_throttle("http://y.example/") >= 1.0

    def test_domains_are_isolated(self):
        t = RateLimitTracker()
        t.update("http://a.example/", 429, {"Retry-After": "60"})
        assert t.should_throttle("http://b.example/") == 0.0

    def test_unknown_domain_ready(self):
        assert RateLimitTracker().should_throttle("http://fresh.example/") == 0.0


class TestUserAgentRotation:
    def test_rotates_distinct_agents(self):
        uas = [get_random_ua() for _ in range(8)]
        assert len(set(uas)) > 1  # never repeats consecutively


class _FakeResp:
    def __init__(self, status=200, text="<html>ok</html>", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {"Content-Type": "text/html"}


class TestHttpRequestTool:
    def _patch_session(self, monkeypatch, resp):
        sent = {}

        class FakeGlobal:
            cookies = {}

            def request(self, method, url, headers=None, data=None, **kw):
                sent.update(method=method, url=url, headers=headers, data=data)
                return resp

        import medusa.tools.runtime as rt

        monkeypatch.setattr(rt, "global_session", FakeGlobal())
        monkeypatch.setattr(http_tools, "global_session", FakeGlobal())
        return sent

    def test_success_renders_status_headers_body(self, monkeypatch):
        sent = self._patch_session(monkeypatch, _FakeResp())
        out = http_tools.http_request("get", "http://t.local/page")
        assert "Status: 200" in out
        assert "Body:" in out and "ok" in out
        assert sent["method"] == "GET"
        # browser-mimicry defaults applied
        assert "User-Agent" in sent["headers"] and "Chrome" in sent["headers"]["User-Agent"]

    def test_rate_limited_short_circuits(self, monkeypatch):
        self._patch_session(monkeypatch, _FakeResp())
        import medusa.tools.session_aware as sa

        monkeypatch.setattr(sa, "is_rate_limited", lambda url: True)
        out = http_tools.http_request("GET", "http://throttled.local/")
        assert out.startswith("RATE LIMITED")
        # no transport call should have been needed — output is policy, not a response

    def test_transport_error_reported_cleanly(self, monkeypatch):
        class Boom:
            cookies = {}

            def request(self, *a, **k):
                raise ConnectionError("refused")

        import medusa.tools.runtime as rt

        monkeypatch.setattr(rt, "global_session", Boom())
        monkeypatch.setattr(http_tools, "global_session", Boom())
        out = http_tools.http_request("GET", "http://dead.local/")
        assert out.startswith("HTTP Error:") and "refused" in out

    def test_body_forwarded(self, monkeypatch):
        sent = self._patch_session(monkeypatch, _FakeResp())
        http_tools.http_request("POST", "http://t.local/login", body="user=a&pass=b")
        assert sent["data"] == "user=a&pass=b"
        assert sent["method"] == "POST"
