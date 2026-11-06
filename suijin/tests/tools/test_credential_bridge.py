"""Regression: the credential bridge — register → persist → wire.

Three field-class bugs locked here (2026-11-06, found by local
echo-server testing before the auth command was built):

1. http_request WIPED injected credentials — a mid-function
   `req_headers = headers if headers else {}` re-anchored to the raw
   argument AFTER the credential block, so every authenticated
   http_request went out bare while its docstring promised the handoff.
2. TWO credential stores, zero overlap — register_credential wrote an
   in-memory dict in http_replay's process; http_request read the
   file-backed credential_store. Registered creds never reached
   http_request, died with the process, and swarm agents (separate
   processes) could never share a login.
3. register_credential's persist path raised NameError
   (contextlib unimported) and returned an error string callers ignored
   — registrations silently did nothing.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import suijin.modules.credential_store.main as _credstore


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Never touch the operator's real credential store — tests write
    their own, and leak into order-dependent console suites otherwise
    (found the hard way: 7 downstream failures from store pollution)."""
    monkeypatch.setattr(_credstore, "STORE_PATH", tmp_path / "credentials.json")


class _Echo(BaseHTTPRequestHandler):
    def _echo(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body.decode("utf-8", "replace")[:200],
                }
            ).encode()
        )

    do_GET = do_POST = do_PUT = _echo

    def log_message(self, *a):  # silence
        pass


@pytest.fixture(scope="module")
def echo_port():
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.1)
    yield port
    srv.shutdown()


def test_apply_credential_injects_cookie():
    from suijin.server.tools.lib.http_replay import apply_credential, register_credential

    register_credential(name="bridge-test-a", cookies="SID=aaa; HSID=bbb")
    req = apply_credential({"method": "GET", "url": "http://x/y", "headers": {}}, "bridge-test-a")
    cookie = str((req.get("headers") or {}).get("Cookie", ""))
    assert "SID=aaa" in cookie


def test_http_request_delivers_credential_to_the_wire(echo_port):
    """The wipe bug: the cookie must ARRIVE, not just be set locally."""
    from suijin.server.tools.lib.http_replay import register_credential
    from suijin.server.tools.lib.http_tools import http_request

    register_credential(name="bridge-test-wire", cookies="SID=wireproof123; HSID=x")
    resp = http_request("GET", f"http://127.0.0.1:{echo_port}/whoami", credential="bridge-test-wire")
    # raw substring check: the echo body embeds the request headers, so
    # the cookie value appearing ANYWHERE in the response proves it hit
    # the wire (the tool appends notes after the body — don't parse JSON)
    assert "SID=wireproof123" in str(resp)


def test_registered_credential_persists_to_shared_store():
    """The two-stores bug: registration must survive to the file store
    (that's what other processes — swarm agents — read)."""
    from suijin.modules.credential_store.main import creds_get
    from suijin.server.tools.lib.http_replay import register_credential

    r = register_credential(name="bridge-test-persist", cookies="PERSIST=yes123")
    assert "Error" not in r
    rec = json.loads(creds_get("bridge-test-persist"))
    assert "yes123" in str(rec.get("value", ""))


def test_storage_state_bridge(echo_port, tmp_path):
    """A playwright storage_state JSON becomes a usable HTTP credential
    with no retyping — the browser-session → tools handoff."""
    from suijin.server.tools.lib.http_replay import register_credential
    from suijin.server.tools.lib.http_tools import http_request

    state = {
        "cookies": [
            {"name": "SID", "value": "state-bridge-1", "domain": ".example.com", "path": "/"},
            {"name": "HSID", "value": "state-bridge-2", "domain": ".example.com", "path": "/"},
        ],
        "origins": [],
    }
    jar = "; ".join(f"{c['name']}={c['value']}" for c in state["cookies"])
    register_credential(name="bridge-test-state", cookies=jar)
    resp = http_request("GET", f"http://127.0.0.1:{echo_port}/bridge", credential="bridge-test-state")
    assert "state-bridge-1" in str(resp)


def test_two_sessions_differential(echo_port):
    """Two named credentials on one request = the auth_diff primitive."""
    from suijin.server.tools.lib.http_replay import register_credential
    from suijin.server.tools.lib.http_tools import http_request

    register_credential(name="bridge-test-acct-a", cookies="SID=accountA")
    register_credential(name="bridge-test-acct-b", cookies="SID=accountB")
    ra = http_request("GET", f"http://127.0.0.1:{echo_port}/a", credential="bridge-test-acct-a")
    rb = http_request("GET", f"http://127.0.0.1:{echo_port}/b", credential="bridge-test-acct-b")
    assert "accountA" in str(ra) and "accountB" in str(rb)
