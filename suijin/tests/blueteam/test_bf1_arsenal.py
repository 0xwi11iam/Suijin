"""BF1 — the blue arsenal: enforcement plane, tool registry, gated shell.

Proof standard: a tool call's effect is OBSERVABLE at the proxy against
the real Hill lab (block blocks, honeypot serves, canary trips).
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[2] / "lab" / "hill_ctf"


@pytest.fixture(scope="module")
def plane(tmp_path_factory):
    """Real Hill lab on :5987 + real proxy on :5988, isolated state files."""
    tmp = tmp_path_factory.mktemp("bf1")
    os.environ["BLUE_ENFORCEMENT_FILE"] = str(tmp / "enf.json")
    env = {
        **os.environ,
        "PORT": "5987",
        "HILL_NO_INTERNAL": "1",
        "HILL_EVENTS_LOG": str(tmp / "ev.jsonl"),
        "HILL_TRAFFIC_LOG": str(tmp / "tr.jsonl"),
    }
    lab = subprocess.Popen(
        [sys.executable, str(LAB / "app.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(30):
        try:
            urllib.request.urlopen("http://127.0.0.1:5987/health", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    from suijin.modules.blueteam.lib.blue import enforcement

    enforcement._DEFAULT_PATH = None  # re-resolve with the env var set
    from suijin.modules.blueteam.lib.blue.proxy import start_proxy

    proxy = start_proxy(listen_port=5988, target_port=5987, target_host="127.0.0.1", log_path=str(tmp / "proxy.jsonl"))
    time.sleep(0.4)
    yield {"tmp": tmp, "proxy_port": 5988, "enforcement": enforcement}
    proxy.stop()
    lab.kill()
    os.environ.pop("BLUE_ENFORCEMENT_FILE", None)


def _get(port, path, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


class TestEnforcementPlane:
    def test_passthrough_unaffected(self, plane):
        code, body = _get(plane["proxy_port"], "/health")
        assert code == 200 and "the-hill" in body

    def test_block_arms_and_returns_403(self, plane):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_block", {"ip": "10.7.7.7", "reason": "scanner"})
        assert "BLOCKED" in out
        # enforcement.is_blocked is what the proxy's enforce() consults
        assert plane["enforcement"].is_blocked("10.7.7.7")
        act = plane["enforcement"].enforce("GET", "/health", "10.7.7.7", "")
        assert act == {"kind": "block"}

    def test_honeypot_serves_at_proxy(self, plane):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        route_blue_tool("blue_honeypot", {"path": "/admin", "content": '{"app":"HillOps","decoy":true}'})
        code, body = _get(plane["proxy_port"], "/admin")
        assert code == 200 and "HillOps" in body and "decoy" in body
        # the REAL app route (robots) is untouched
        code2, body2 = _get(plane["proxy_port"], "/robots.txt")
        assert code2 == 200 and "Disallow" in body2

    def test_fake_response_status(self, plane):
        plane["enforcement"].fake_response("/gone", "nothing here", 404)
        act = plane["enforcement"].enforce("GET", "/gone", "1.2.3.4", "")
        assert act["kind"] == "respond" and act["status"] == 404

    def test_redirect_by_ip(self, plane):
        plane["enforcement"].redirect_ip("10.8.8.8", "https://sink.example/trap")
        act = plane["enforcement"].enforce("GET", "/anything", "10.8.8.8", "")
        assert act["kind"] == "redirect" and "sink.example" in act["url"]

    def test_unblock(self, plane):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        route_blue_tool("blue_block", {"ip": "10.9.9.9"})
        assert plane["enforcement"].is_blocked("10.9.9.9")
        route_blue_tool("blue_unblock", {"ip": "10.9.9.9"})
        assert not plane["enforcement"].is_blocked("10.9.9.9")

    def test_longest_prefix_wins(self, plane):
        plane["enforcement"].serve_honeypot("/api", '{"a":1}')
        plane["enforcement"].serve_honeypot("/api/vault", '{"vault":true}')
        act = plane["enforcement"].enforce("GET", "/api/vault/x", "1.1.1.1", "")
        assert "vault" in act["body"]


class TestCanaries:
    def test_arm_and_trip(self, plane):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_arm_canary", {"token": "AKIATESTCANARY42", "note": "staged in doc"})
        assert "CANARY" in out
        assert plane["enforcement"].canary_hits() == []
        # the request carrying it trips (enforce's canary check runs on pass-through)
        plane["enforcement"].enforce("POST", "/hill/login", "9.9.9.9", "user=x&key=AKIATESTCANARY42")
        hits = plane["enforcement"].canary_hits()
        assert hits and hits[-1]["ip"] == "9.9.9.9" and "staged" in hits[-1]["note"]

    def test_canary_hits_tool_renders(self, plane):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_canary_hits", {})
        assert "9.9.9.9" in out

    def test_short_token_refused(self, plane):
        out = plane["enforcement"].arm_canary("abc")
        assert out.startswith("Error")


class TestGatedShell:
    def test_dangerous_refused(self):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_shell", {"cmd": "rm -rf /"})
        assert out.startswith("REFUSED")

    def test_benign_runs(self):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_shell", {"cmd": "echo blue-freedom"})
        assert "blue-freedom" in out and "[exit 0]" in out

    def test_kill_switch(self, monkeypatch):
        from suijin.modules.blueteam.lib.blue import tools as bt

        monkeypatch.setenv("SUIJIN_AUTO_APPROVE", "true")
        # even with the override the DANGEROUS pattern here is mkfs — use a
        # benign-but-flagged pattern to prove the override path executes
        out = bt._blue_shell(args={"cmd": "echo shutdown-check"})
        assert "shutdown-check" in out


class TestRegistryShape:
    def test_namespaced_no_kernel_leak(self):
        """The deathmatch guard: blue tools must NOT be in red's dispatch
        routes (red+blue share one process during a war)."""
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.tools.lib.dispatch import _build_routes

        routes = set(_build_routes({}).keys())
        from suijin.modules.blueteam.lib.blue.tools import BLUE_TOOLS

        leaked = sorted(set(BLUE_TOOLS) & routes)
        assert not leaked, f"blue tools leaked into red dispatch: {leaked}"

    def test_every_tool_routes(self):
        from suijin.modules.blueteam.lib.blue.tools import BLUE_TOOLS, route_blue_tool

        for name in BLUE_TOOLS:
            out = route_blue_tool(name, {})
            assert isinstance(out, str) and out  # callable, never raises

    def test_unknown_tool_guidance(self):
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_nope", {})
        assert "Unknown blue tool" in out

    def test_prompt_catalog_renders(self):
        from suijin.modules.blueteam.lib.blue.tools import render_blue_tools

        cat = render_blue_tools()
        assert "BLUE TOOLS" in cat and "blue_block" in cat and "blue_honeypot" in cat


class TestForceRotate:
    def test_semaphore_round_trip(self, tmp_path, monkeypatch):
        import suijin.modules.platform.lib.constants as c

        monkeypatch.setattr(c, "TMP_DIR", tmp_path)
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        out = route_blue_tool("blue_force_rotate", {"reason": "exfil suspected"})
        assert "FORCE-ROTATE" in out
        assert (tmp_path / "hill_force_rotate").exists()
