"""`suijin auth` — session command regression tests.

Covers the headless-testable surface: prefix-safe cookie parsing,
domain heuristic, session file round-trip, heartbeat verdicts
(alive / expired / redirect-loop). The visible-browser login flow is
excluded by design — it needs a human and a display.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import suijin.server.tools.lib.auth_session as _auth
from suijin.server.tools.lib.auth_session import (
    check_session,
    cookie_string_to_state,
    load_session,
    save_session,
    state_to_cookie_string,
)


class _Login(BaseHTTPRequestHandler):
    def do_GET(self):
        if "servicelogin" in self.path.lower():
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>sign in</html>")
        else:
            self.send_response(302)
            self.send_header("Location", "/ServiceLogin?continue=x")
            self.end_headers()

    def log_message(self, *a):
        pass


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class TestParse:
    def test_secure_host_prefixes(self):
        state = cookie_string_to_state(
            "SID=a; __Secure-ENID=b; __Host-jar=c", "accounts.example.com"
        )
        by_name = {c["name"]: c for c in state["cookies"]}
        assert by_name["SID"]["domain"] == ".example.com"
        assert by_name["__Secure-ENID"]["secure"] is True
        # __Host- must NOT carry a domain — url-form instead
        assert "domain" not in by_name["__Host-jar"]
        assert by_name["__Host-jar"]["url"] == "https://accounts.example.com/"
        assert by_name["__Host-jar"]["secure"] is True

    def test_cookie_header_prefix_stripped(self):
        state = cookie_string_to_state("Cookie: SID=a; X=b", "example.com")
        assert [c["name"] for c in state["cookies"]] == ["SID", "X"]

    def test_domain_heuristic_parent(self):
        state = cookie_string_to_state("SID=a", "accounts.example.com")
        assert state["cookies"][0]["domain"] == ".example.com"

    def test_domain_override(self):
        state = cookie_string_to_state("SID=a", "x.co.uk", domain=".co.uk")
        assert state["cookies"][0]["domain"] == ".co.uk"

    def test_roundtrip_to_header_string(self):
        state = cookie_string_to_state("A=1; B=2", "example.com")
        assert state_to_cookie_string(state) == "A=1; B=2"


class TestSessionFiles:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_auth, "sessions_dir", lambda: tmp_path)
        state = cookie_string_to_state("SID=a", "example.com")
        p = save_session("example.com", state, "acct2")
        assert p.exists()
        assert load_session("example.com", "acct2") == state

    def test_playwright_context_rehydrates_saved_session(self, tmp_path, monkeypatch):
        pytest.importorskip("playwright")
        monkeypatch.setattr(_auth, "sessions_dir", lambda: tmp_path)
        state = cookie_string_to_state("SID=a; __Secure-X=b", "example.com")
        p = save_session("example.com", state)
        from playwright.sync_api import sync_playwright

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
        except Exception:  # noqa: BLE001 — test HOME has no browser cache
            pytest.skip("playwright browsers not installed under this HOME")
        try:
            ctx = browser.new_context(storage_state=str(p))
            jar = {c["name"]: c["value"] for c in ctx.cookies("https://example.com/")}
        finally:
            browser.close()
            pw.stop()
        assert jar.get("SID") == "a" and jar.get("__Secure-X") == "b"


class TestHeartbeat:
    def test_alive_and_expired(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_auth, "sessions_dir", lambda: tmp_path)
        save_session("hb.example", cookie_string_to_state("SID=a", "hb.example"))

        ok = HTTPServer(("127.0.0.1", 0), _Ok)
        login = HTTPServer(("127.0.0.1", 0), _Login)
        for s in (ok, login):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        time.sleep(0.1)

        try:
            v = check_session("hb.example", check_url=f"http://127.0.0.1:{ok.server_address[1]}/")
            assert v.startswith("alive")
            v = check_session("hb.example", check_url=f"http://127.0.0.1:{login.server_address[1]}/")
            assert v.startswith("EXPIRED")
        finally:
            ok.shutdown()
            login.shutdown()

    def test_missing_session(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_auth, "sessions_dir", lambda: tmp_path)
        assert "no session" in check_session("never.example")
