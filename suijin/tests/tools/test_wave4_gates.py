"""Wave 4 gym — coverage ledger, the completion gate, surface expansion,
same-surface stall detection. The premature-closure hole is CLOSED."""

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

from suijin.modules.agent.lib.mode_governor import update_queue  # noqa: E402
from suijin.modules.tools.lib.coverage import (  # noqa: E402
    asset_of,
    completion_blocked,
    coverage_check,
    mark,
    note,
    untested,
)

PUB = 5985
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
    ws = tmp_path_factory.mktemp("ws4")
    _kill_port(PUB)
    app_py = str(Path(__file__).resolve().parents[2] / "lab" / "citadel" / "app.py")
    proc = subprocess.Popen(
        [sys.executable, app_py],
        env={**os.environ, "PORT": str(PUB), "CITADEL_DB": "/tmp/suijin_cov_test.db",
             "CITADEL_TRAFFIC": "/tmp/cov_test_traffic.jsonl", "CITADEL_RATE_LIMIT": "100000",
             "CITADEL_NO_INTERNAL": "1", "SUIJIN_WORKSPACE": str(ws)},
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
    from suijin.modules.tools.lib.http_replay import _BUDGET

    _BUDGET["remaining"] = 5000
    yield proc
    proc.send_signal(signal.SIGTerm if False else signal.SIGTERM)
    time.sleep(0.5)
    _kill_port(PUB)


class TestLedger:
    def test_asset_key_shape(self):
        assert asset_of("https://t.com/api/docs/d-8b2e40d1") == "https://t.com/api/docs/:id"

    def test_not_vulnerable_requires_evidence(self):
        out = mark(BASE, "sqli", "tested_not_vulnerable", evidence="short", request_sent="GET /x")
        assert "FALSE record" in out
        out = mark(BASE, "sqli", "tested_not_vulnerable",
                   evidence="fired three encoded boolean pairs; all responses byte-identical to the noise floor",
                   request_sent="GET /api/items?category=hardware%27")
        assert "→ tested_not_vulnerable" in out

    def test_unknown_class_rejected(self):
        assert mark(BASE, "warp_drive", "tested_vulnerable").startswith("Error")

    def test_untested_lists_gaps(self):
        ute = untested([BASE])
        assert any(u["vuln_class"] == "idor" for u in ute)

    def test_wide_notes_dedupe(self):
        assert "recorded" in note("wide", BASE, "JWT HS256 with strong secret — token crypto OK origin-wide")
        assert "recorded" in note("wide", BASE, "JWT HS256 with strong secret — token crypto OK origin-wide")
        s = coverage_check(action="summary")
        assert s.count("JWT HS256") == 1  # recorded once, not twice

    def test_dispatch_route(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("coverage_check", {"action": "mark", "asset": BASE, "vuln_class": "xss",
                                            "status": "not_applicable"}, {})
        assert "not_applicable" in str(out)


class TestCompletionGate:
    def test_gate_blocks_with_gaps(self, citadel):
        blocked = completion_blocked([BASE])
        assert blocked is not None and "COMPLETION REFUSED" in blocked

    def test_gate_opens_when_covered(self, citadel):
        # mark ALL classes for this asset → gate opens
        from suijin.modules.tools.lib import coverage as cov

        ev = "verified by direct request and response diff — see traffic store"
        for vc in cov.CLASSES:
            mark(BASE, vc, "not_applicable", evidence=ev, request_sent="GET /")
        assert completion_blocked([BASE]) is None

    def test_think_level_complete_refused(self):
        """The REAL gate: model-initiated complete action returns a refusal
        message instead of completion_reason while surfaces remain."""
        import asyncio

        from suijin.modules.agent.lib.nodes.think_node import think_node

        async def fake_gen(messages, config):
            return json.dumps({
                "action": "complete",
                "completion_reason": "Objective complete",
                "thought": "done",
            })

        state = {
            "current_phase": "exploitation",
            "current_iteration": 12,
            "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "o"}],
            "execution_trace": [],
            "_attack_queue": [
                {"surface": "http://t/a", "cls": "web", "tried": False, "iter": 1},
                {"surface": "http://t/b", "cls": "web", "tried": False, "iter": 2},
                {"surface": "http://t/c", "cls": "web", "tried": False, "iter": 3},
            ],
            "_objective": "test https://t.com",
            "max_iterations": 100,
        }
        result = asyncio.run(think_node(state, generate_fn=fake_gen))
        assert not result.get("completion_reason"), "completion must be REFUSED with untried surfaces"
        assert any("COMPLETION REFUSED" in str(m.get("content")) for m in result.get("messages", []))


class TestSurfaceExpand:
    def test_sibling_enumeration_finds_existing(self, citadel):
        from suijin.modules.tools.lib.surface_expand import surface_expand

        out = surface_expand(url=f"{BASE}/api/v2/health", allow_internal=True,
                             names=["health", "executive", "ghost", "settings"])
        res = json.loads(out)
        paths = {r["path"]: r["status"] for r in res["existing"]}
        assert paths.get("/api/v2/executive") in (200, 403)  # exists (role-gated)
        assert paths.get("/api/v2/ghost") is None  # correctly not listed (404)

    def test_pattern_placeholder(self, citadel):
        from suijin.modules.tools.lib.surface_expand import surface_expand

        out = surface_expand(url=f"{BASE}/modals/{{name}}", allow_internal=True,
                             names=["login", "ghost"])
        res = json.loads(out)
        assert res["probed"] >= 2


class TestSurfaceStall:
    def test_grind_triggers_pivot_message(self):
        state = {"_attack_queue": [], "execution_trace": []}
        url = "http://t/api/items"
        for i in range(4):
            result = {
                "_current_step": {"tool_name": "http_request", "tool_args": {"url": url}},
                "current_iteration": i + 1,
                "_target_grew_last_step": False,
                "execution_trace": [],
                "messages": [],
            }
            state = {"_attack_queue": update_queue(state, result), "_surface_attempts": result.get("_surface_attempts")}
        msgs = [m for m in result["messages"] if "SURFACE STALL" in str(m.get("content"))]
        assert msgs, "4 no-growth attempts must fire the pivot directive"

    def test_progress_does_not_trigger(self):
        state = {"_attack_queue": [], "execution_trace": []}
        url = "http://t/api/items"
        for i in range(4):
            result = {
                "_current_step": {"tool_name": "http_request", "tool_args": {"url": url}},
                "current_iteration": i + 1,
                "_target_grew_last_step": (i == 1),  # board grew once
                "execution_trace": [],
                "messages": [],
            }
            state = {"_attack_queue": update_queue(state, result), "_surface_attempts": result.get("_surface_attempts")}
        assert not any("SURFACE STALL" in str(m.get("content")) for m in result["messages"])
