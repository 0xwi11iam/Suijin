"""bypass_403 — the 403 breaker battery, against a fake access-control server."""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class _ACLHandler(BaseHTTPRequestHandler):
    """403 on the plain path; 200 for X-Original-URL carriers and /x/. paths."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def _handle(self, method):
        if self.headers.get("X-Original-URL") or self.path.endswith("/."):
            self._resp(200, "admin dashboard")
        else:
            self._resp(403, "forbidden")

    def _resp(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


@pytest.fixture()
def acl_server():
    srv = HTTPServer(("127.0.0.1", 0), _ACLHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch):
    # pacing/rate-limit live inside http_request — neutralize for tests
    import suijin.modules.platform.lib.stealth as st
    import suijin.modules.tools.lib.session_aware as sa

    monkeypatch.setattr(sa, "is_rate_limited", lambda url: False)
    monkeypatch.setattr(
        sa,
        "get_session",
        lambda url: type(
            "S",
            (),
            {
                "get_cookie_string": lambda self: "",
                "update_from_response": lambda self, h, t: None,
                "touch": lambda self: None,
            },
        )(),
    )
    monkeypatch.setattr(sa, "record_response", lambda url, code, headers: None)
    monkeypatch.setattr(st, "pace", lambda: None)
    monkeypatch.setattr(st, "browser_identity", lambda: {})


def test_battery_finds_bypasses(acl_server):
    from suijin.modules.tools.lib.bypass_403 import bypass_403

    out = bypass_403(f"http://127.0.0.1:{acl_server.server_port}/admin")
    assert "bypass_403 battery" in out
    assert "VERDICT" in out
    # X-Original-URL and the /x/. path trick must both register as wins
    assert "X-Original-URL" in out
    assert "/x/." in out
    assert "2/" in out  # 2 bypasses found


def test_zero_bypasses_reports_enforced(monkeypatch):
    import suijin.modules.tools.lib.bypass_403 as b

    monkeypatch.setattr(
        "suijin.modules.tools.lib.http_tools.http_request",
        lambda m, u, headers=None, body="": "Status: 403\nBody:\nforbidden",
    )
    out = b.bypass_403("https://example.com/secret")
    assert "0 bypasses" in out
    assert "enforced" in out


def test_bad_url_is_clean_error():
    from suijin.modules.tools.lib.bypass_403 import bypass_403

    out = bypass_403("")
    assert out.startswith("Error:")


def test_dispatch_route_registered():
    from suijin.modules.tools.lib.dispatch import route_tool

    routes_present = "bypass_403"
    out = route_tool(routes_present, {"url": ""}, {})
    assert str(out).startswith("Error:")  # empty URL → clean error string, no raise
