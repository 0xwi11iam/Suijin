"""Wave 3 gym — web_session: automatic cross-credential model, the IDOR
worklist, hidden-params correlation, UI-field capture from the browser."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.http_replay import _BUDGET, http_replay, register_credential  # noqa: E402
from suijin.modules.tools.lib.web_session import (  # noqa: E402
    _endpoint_key,
    cross_credential_shortlist,
    hidden_params,
    record_ui_fields,
    web_session,
)

PUB = 5984
BASE = f"http://127.0.0.1:{PUB}"


def _kill_port(port):
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout.strip()
        for pid in out.splitlines():
            subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception:
        pass


@pytest.fixture(scope="module")
def citadel(tmp_path_factory):
    # isolate the session store: run with a scratch workspace via env
    store_dir = tmp_path_factory.mktemp("ws3")
    _kill_port(PUB)
    app_py = str(Path(__file__).resolve().parents[2] / "lab" / "citadel" / "app.py")
    proc = subprocess.Popen(
        [sys.executable, app_py],
        env={**os.environ, "PORT": str(PUB), "CITADEL_DB": "/tmp/suijin_ws_test.db",
             "CITADEL_TRAFFIC": "/tmp/ws_test_traffic.jsonl", "CITADEL_RATE_LIMIT": "100000",
             "CITADEL_NO_INTERNAL": "1", "SUIJIN_WORKSPACE": str(store_dir)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            if requests.get(f"{BASE}/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        pytest.fail("citadel did not boot")
    _BUDGET["remaining"] = 5000
    yield proc
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.5)
    _kill_port(PUB)


def _login(user, pw):
    out = http_replay(
        method="POST", url=f"{BASE}/login", allow_internal=True,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, body=f"u={user}&p={pw}",
    )
    return json.loads(json.loads(out)["body"])["token"]


class TestShapeKey:
    def test_ids_collapse(self):
        assert _endpoint_key("GET /api/docs/d-8b2e40d1") == _endpoint_key("GET /api/docs/d-9c5f12ab")
        assert _endpoint_key("GET /users/1/orders") == _endpoint_key("GET /users/2/orders")

    def test_different_paths_differ(self):
        assert _endpoint_key("GET /api/docs/x") != _endpoint_key("GET /api/items/x")


class TestRoleCycling:
    def test_cross_credential_shortlist_finds_idor(self, citadel):
        alice_tok = _login("alice", "alice123")
        ceo_tok = _login("ceo", "Zx!9topSecret")
        register_credential("alice", headers={"X-Session": alice_tok})
        register_credential("ceo", headers={"X-Session": ceo_tok})

        # both credentials browse the same surfaces (role cycling)
        for cred in ("alice", "ceo"):
            http_replay(url=f"{BASE}/api/docs/d-8b2e40d1", allow_internal=True, credential=cred)
            http_replay(url=f"{BASE}/api/docs/d-9c5f12ab", allow_internal=True, credential=cred)
            http_replay(url=f"{BASE}/api/v2/health", allow_internal=True, credential=cred)

        out = web_session(action="summary")
        assert "access-control worklist" in out
        assert "/api/docs/:id" in out  # the shape reached by 2+ credentials
        assert "IDOR test" in out  # the exact replay instruction
        short = cross_credential_shortlist()
        shapes = [s["endpoint_shape"] for s in short]
        assert "GET /api/docs/:id" in shapes

    def test_auto_attribution_without_registration(self, citadel):
        # unregistered sends are attributed by their auth-header label
        http_replay(url=f"{BASE}/api/v2/health", allow_internal=True,
                    headers={"X-Session": "auto-tok-123"})
        out = web_session(action="observations")
        assert "auto-tok-123" in out or "x-session:auto" in out.lower()

    def test_hidden_params_correlation(self, citadel):
        # the login UI exposes u+p; the register API sends username+password+role
        # (role is the mass-assignment target that never appears in any UI)
        record_ui_fields(f"{BASE}/login", [{"name": "u", "type": "text", "hidden": False},
                                           {"name": "p", "type": "password", "hidden": False}])
        http_replay(method="POST", url=f"{BASE}/api/register", allow_internal=True,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"username": "hp_test", "password": "x", "role": "user"}))
        hp = hidden_params()
        flat = [p for h in hp for p in h["params_not_in_ui"]]
        assert "role" in flat  # the UI never exposed it — mass-assignment target


class TestToolSurface:
    def test_dispatch_route(self, citadel):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("web_session", {"action": "summary"}, {})
        assert "worklist" in str(out)

    def test_browser_snapshot_feeds_ui_fields(self, citadel):
        # drive the real browser at the login page; the snapshot hook records fields
        from suijin.modules.mcp_playwright.main import mcp_browser_close, mcp_browser_goto, mcp_browser_snapshot
        from suijin.modules.tools.lib import web_session as ws

        goto = mcp_browser_goto(f"{BASE}/login")
        assert goto.startswith("Loaded")
        snap = mcp_browser_snapshot()
        assert "INPUT" in snap
        mcp_browser_close()
        # the UI fields were captured for /login
        assert any(f.get("name") == "u" or f.get("name") == "p" for f in ws._UI_FIELDS.get("/login", []))
