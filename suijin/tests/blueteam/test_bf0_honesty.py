"""BF0 — blue honesty: no zero-defense verdicts, honest counters, boundaries."""

import json
from pathlib import Path

import pytest

from suijin.modules.blueteam.lib.blue.tui.feed import LiveFeed


@pytest.fixture()
def lab_feed(tmp_path, monkeypatch):
    """A LiveFeed wired to the Hill conventions (isolated tarpit file),
    no SOC tiers — the minimum to drive process_request honestly."""
    from suijin.modules.blueteam.lib.blue.tui.feed import FeedConfig

    cfg = FeedConfig(baseline_requests=2, ai_analysis_enabled=False)
    engine = type("E", (), {"analyze_request": None, "total_analyses": 0, "total_cost_usd": 0.0, "target_path": ""})()
    sub = type(
        "S",
        (),
        {
            "find_for_request": lambda self, p: None,
            "get_subagent_notes": lambda self, p: "",
            "record_anomaly": lambda self, p, v: None,
            "get_summary": lambda self: {"total": 0},
        },
    )()
    f = LiveFeed(ai_engine=engine, subagent_manager=sub, config=cfg)
    f.TARPIT_FILE = str(tmp_path / "tarpit.json")
    return f


def _sql_request(ip="10.1.1.1"):
    return {
        "method": "GET",
        "path": "/hill/api/docs",
        "query": {"q": "' UNION SELECT 1--"},
        "body": "",
        "ip": ip,
        "user_agent": "x",
        "headers": {},
    }


class TestNoZeroDefense:
    def test_review_action_gets_fallback_tarpit(self, lab_feed, monkeypatch):
        """The audit's bug #1: AI-down returns FLAGGED+REVIEW which matched
        no verb -> zero defense. The fix: any FLAGGED decision that matched
        no action verb applies the fallback tarpit anyway."""
        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult

        result = AIAnalysisResult(
            request_id=1,
            method="GET",
            path="/x",
            ip="10.9.9.9",
            verdict="FLAGGED",
            score=9,
            action="REVIEW",
            reasoning="provider down",
            attack_analysis="x",
            attacker_assessment="y",
            commands_run=[],
            code_changes=[],
        )
        lab_feed._execute_ai_decision(result, "10.9.9.9", ["sql_injection"], 9)
        state = json.loads(Path(lab_feed.TARPIT_FILE).read_text())
        assert "10.9.9.9" in state  # defended
        assert "fallback" in result.action

    def test_log_action_also_defends(self, lab_feed):
        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult

        result = AIAnalysisResult(
            request_id=2,
            method="GET",
            path="/x",
            ip="10.9.9.8",
            verdict="FLAGGED",
            score=6,
            action="LOG",
            reasoning="watching",
            attack_analysis="x",
            attacker_assessment="y",
            commands_run=[],
            code_changes=[],
        )
        lab_feed._execute_ai_decision(result, "10.9.9.8", ["xss_attempt"], 6)
        assert "10.9.9.8" in json.loads(Path(lab_feed.TARPIT_FILE).read_text())

    def test_block_action_still_blocks_logged_only(self, lab_feed):
        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult

        result = AIAnalysisResult(
            request_id=3,
            method="GET",
            path="/x",
            ip="10.9.9.7",
            verdict="FLAGGED",
            score=10,
            action="BLOCK",
            reasoning="hard block",
            attack_analysis="x",
            attacker_assessment="y",
            commands_run=[],
            code_changes=[],
        )
        lab_feed.blocking_enabled = False  # default: operator-gated
        lab_feed._execute_ai_decision(result, "10.9.9.7", ["xxe_attempt"], 10)
        # logged-only when blocking disabled — no firewall call, no counter bump
        assert lab_feed.stats_blocked == 0

    def test_blocked_increments_when_enabled(self, lab_feed, monkeypatch):
        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult

        ran = []
        import subprocess as sp

        monkeypatch.setattr(sp, "run", lambda *a, **k: ran.append(a) or type("R", (), {"returncode": 0})())
        result = AIAnalysisResult(
            request_id=4,
            method="GET",
            path="/x",
            ip="10.9.9.6",
            verdict="FLAGGED",
            score=10,
            action="BLOCK",
            reasoning="b",
            attack_analysis="x",
            attacker_assessment="y",
            commands_run=[],
            code_changes=[],
        )
        lab_feed.blocking_enabled = True
        lab_feed._execute_ai_decision(result, "10.9.9.6", ["sql_injection"], 10)
        assert lab_feed.stats_blocked == 1 and ran


class TestHonestCounters:
    def test_pattern_attack_counts_detected_and_tarpitted(self, lab_feed):
        import asyncio

        # establish the baseline first (2 benigns), then attack
        for _ in range(2):
            asyncio.run(
                lab_feed.process_request(
                    {"method": "GET", "path": "/", "ip": "10.3.3.3", "user_agent": "x", "headers": {}}
                )
            )
        out = asyncio.run(lab_feed.process_request(_sql_request()))
        assert out is not None
        s = lab_feed.get_stats()
        assert s["detected"] == 1 and s["tarpitted"] == 1  # flag AND enforcement
        assert "10.1.1.1" in json.loads(Path(lab_feed.TARPIT_FILE).read_text())

    def test_benign_not_counted(self, lab_feed):
        import asyncio

        asyncio.run(
            lab_feed.process_request({"method": "GET", "path": "/", "ip": "10.2.2.2", "user_agent": "x", "headers": {}})
        )
        s = lab_feed.get_stats()
        assert s["detected"] == 0 and s["tarpitted"] == 0

    def test_deceived_only_on_real_deploy(self, lab_feed):
        assert lab_feed.get_stats()["deceived"] == 0  # no deploy, no count


class TestBoundaryFix:
    def test_sibling_not_swallowed(self):
        from suijin.modules.blueteam.lib.blue.subagent_manager import EndpointSubagent, SubagentManager

        m = SubagentManager.__new__(SubagentManager)
        m.subagents = {
            "a": EndpointSubagent(agent_id="a", endpoint={"path": "/api/users/<int:uid>"}, rank=1),
            "b": EndpointSubagent(agent_id="b", endpoint={"path": "/api/users_export"}, rank=2),
        }
        assert m.find_for_request("/api/users_export").agent_id == "b"  # was 'a'
        assert m.find_for_request("/api/users/42").agent_id == "a"

    def test_deep_var_paths(self):
        from suijin.modules.blueteam.lib.blue.subagent_manager import EndpointSubagent, SubagentManager

        m = SubagentManager.__new__(SubagentManager)
        m.subagents = {
            "c": EndpointSubagent(agent_id="c", endpoint={"path": "/api/documents/<int:doc_id>/download"}, rank=1)
        }
        assert m.find_for_request("/api/documents/7/download").agent_id == "c"
        assert m.find_for_request("/api/documents/7/download/extra") is None  # overran

    def test_flagged_ips_instance_state(self):
        f1 = LiveFeed.__new__(LiveFeed)
        f2 = LiveFeed.__new__(LiveFeed)
        assert (
            not hasattr(f1, "_flagged_ips")
            or f1.__dict__.get("_flagged_ips") is not f2.__dict__.get("_flagged_ips")
            or True
        )
        # the real guarantee: instances initialized via __init__ own their dicts
        from suijin.modules.blueteam.lib.blue.tui.feed import FeedConfig

        class E:
            total_analyses = 0
            total_cost_usd = 0.0

        class S:
            def get_summary(self):
                return {}

        a = LiveFeed(ai_engine=E(), subagent_manager=S(), config=FeedConfig())
        b = LiveFeed(ai_engine=E(), subagent_manager=S(), config=FeedConfig())
        a._flagged_ips["x"] = 1
        assert "x" not in b._flagged_ips
