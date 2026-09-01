"""Mode governor — recon→exploit machinery: surface queue, switch decision,
best-fit doctrine, scoreboard."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.agent.lib.mode_governor import (  # noqa: E402
    best_skill_for,
    govern,
    harvest_surfaces,
    posture_config,
    scoreboard,
    untried,
    update_queue,
)


def _state(phase="informational", iters=8, queue=None, trace=None):
    return {
        "current_phase": phase,
        "current_iteration": iters,
        "_attack_queue": queue or [],
        "execution_trace": trace or [],
    }


class TestPosture:
    def test_defaults(self):
        pc = posture_config(None)
        assert pc["posture"] == "assertive" and pc["recon_cap"] == 6

    def test_recon_patient(self):
        pc = posture_config({"posture": "recon"})
        assert pc["recon_cap"] == 20


class TestSurfaceQueue:
    def test_http_surfaces_harvested(self):
        r = {"_current_step": {"tool_name": "http_request", "tool_args": {"url": "http://t/login"}}}
        s = harvest_surfaces(r)
        assert any("login" in x["surface"] for x in s)

    def test_queue_dedup_and_untried(self):
        state = _state()
        r = {"_current_step": {"tool_name": "http_request", "tool_args": {"url": "http://t/login"}}}
        q = update_queue(state, r)
        q = update_queue({"_attack_queue": q}, r)  # same surface again
        assert len(q) == 1
        assert len(untried(q)) == 1

    def test_finding_retires_surface(self):
        q = [{"surface": "http://t/login", "cls": "web", "tried": False, "iter": 1}]
        r = {"execution_trace": [{"tool_name": "record_finding"}], "current_iteration": 3}
        q2 = update_queue({"_attack_queue": q}, r)
        assert len(untried(q2)) == 0


class TestGovernor:
    def test_switches_on_cap_with_untried(self):
        q = [
            {"surface": "http://t/login", "cls": "web", "tried": False, "iter": 1},
            {"surface": "http://t/api/users", "cls": "web", "tried": False, "iter": 2},
        ]
        d = govern(_state(iters=7, queue=q), {"posture": "assertive"})
        assert d is not None
        assert d["current_phase"] == "exploitation"
        assert d["attack_path_type"]  # doctrine swapped
        assert any("MODE CHANGE" in m["content"] for m in d["messages"])

    def test_no_switch_without_surfaces(self):
        d = govern(_state(iters=50, queue=[]), {})
        assert d is None

    def test_no_switch_in_exploitation(self):
        q = [{"surface": "x", "cls": "web", "tried": False, "iter": 1}]
        d = govern(_state(phase="exploitation", queue=q), {})
        assert d is None

    def test_recon_posture_is_patient(self):
        q = [{"surface": "http://t/a", "cls": "web", "tried": False, "iter": 1},
             {"surface": "http://t/b", "cls": "web", "tried": False, "iter": 2}]
        assert govern(_state(iters=7, queue=q), {"posture": "recon"}) is None
        assert govern(_state(iters=21, queue=q), {"posture": "recon"}) is not None

    def test_recent_surface_blocks_stall_switch(self):
        q = [{"surface": "http://t/a", "cls": "web", "tried": False, "iter": 7},
             {"surface": "http://t/b", "cls": "web", "tried": False, "iter": 8}]
        assert govern(_state(iters=8, queue=q), {}) is None  # still finding things


class TestDoctrine:
    def test_login_maps_to_sqli(self):
        s = _state(queue=[{"surface": "http://t/login?user=", "cls": "web", "tried": False}])
        assert best_skill_for(s) == "sql_injection"

    def test_upload_maps_to_upload_skill(self):
        s = _state(queue=[{"surface": "http://t/profile/avatar upload", "cls": "web", "tried": False}] * 3)
        assert best_skill_for(s) == "file_upload"

    def test_empty_defaults_to_rce(self):
        assert best_skill_for(_state()) == "rce"


class TestScoreboard:
    def test_line_shape(self):
        s = _state(queue=[{"surface": "a", "cls": "web", "tried": False}], iters=3)
        sb = scoreboard(s)
        assert "untried_surfaces=1" in sb and "mode=informational" in sb
