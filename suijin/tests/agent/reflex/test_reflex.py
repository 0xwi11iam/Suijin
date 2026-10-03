"""The reflex layer — tests.

Core contract: abstain dominates on healthy traces, misses fall down
the ladder, qset mismatches disable cleanly, shadow closes outcomes,
and the three seams (supervisor/drifter/oracle) change NOTHING when
`decision.enabled` is absent.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from suijin.modules.agent.lib.reflex.core import client as rc  # noqa: E402
from suijin.modules.agent.lib.reflex.core import shadow  # noqa: E402
from suijin.modules.agent.lib.reflex.core.questions import QSET_VERSION, questions  # noqa: E402
from suijin.modules.agent.lib.reflex.drifter import align  # noqa: E402
from suijin.modules.agent.lib.reflex.oracle import adjudicate, triage  # noqa: E402
from suijin.modules.agent.lib.reflex.supervisor.verdict import (  # noqa: E402
    phrase as sv_phrase,
)
from suijin.modules.agent.lib.reflex.supervisor.verdict import (
    reflex_enabled as sv_enabled,
)
from suijin.modules.agent.lib.reflex.supervisor.verdict import (
    verdict as sv,
)


@pytest.fixture()
def shadow_file(tmp_path):
    shadow.set_shadow_path(tmp_path / "shadow.jsonl")
    yield tmp_path / "shadow.jsonl"
    shadow.set_shadow_path(None)


# ── core ─────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_every_question_carries_abstain(self):
        for q in questions().values():
            assert "none" in q.choices, q.qid

    def test_qset_versioned(self):
        assert QSET_VERSION == 1


class TestLocalClassifier:
    def test_healthy_trace_abstains_dominantly(self):
        f = {
            "phase": "recon",
            "iteration": 10,
            "iterations_since_finding": 3,
            "repeat_pressure": 0.1,
            "phase_tool_mismatch": 0,
            "tool_fail_cluster": 0,
            "chain_candidates": 0,
            "cost_rate_usd": 0.01,
            "drift_score": 0.0,
            "unheeded_count": 0,
        }
        out = rc.decide("supervisor.verdict", f, {"decision": {"enabled": True, "on_fail": "rules"}})
        assert out["choice"] == "none" and out["p"] >= 0.8

    def test_repeat_loop_detected(self):
        f = {"repeat_pressure": 0.8}
        out = rc.local_classify("supervisor.verdict", f)
        assert out == ("repeat", pytest.approx(0.87, abs=0.01))

    def test_unknown_qid_is_miss(self):
        assert rc.decide("nope.question", {}, {}) is None

    def test_qset_mismatch_disables(self):
        # a remote answering an old qset is a miss, never a guess
        c = rc.DecisionClient(
            {"decision": {"enabled": True}},
            transport=lambda ep, pl, timeout: json.dumps(
                {"qset": 0, "answers": [{"qid": "supervisor.verdict", "choice": "drift", "p": 1.0}]}
            ),
        )
        out = c.decide("supervisor.verdict", {})
        assert out["engine"] == "local"  # fell through to the deterministic engine

    def test_remote_choice_outside_schema_is_miss(self):
        c = rc.DecisionClient(
            {"decision": {"enabled": True}},
            transport=lambda ep, pl, timeout: json.dumps(
                {"qset": 1, "answers": [{"qid": "supervisor.verdict", "choice": "invented", "p": 1.0}]}
            ),
        )
        out = c.decide("supervisor.verdict", {})
        assert out["engine"] == "local"

    def test_default_config_is_disabled(self):
        assert rc.decision_config(None)["enabled"] is False
        assert rc.decision_config({})["enabled"] is False


# ── supervisor pipeline ──────────────────────────────────────────────────


class TestSupervisorVerdict:
    def test_healthy_act_false(self):
        state = {"current_phase": "recon", "current_iteration": 10, "findings": [{"iteration": 8}]}
        trace = [
            {"tool_name": "http_request", "args": "a1", "success": True},
            {"tool_name": "search_kb", "args": "b", "success": True},
        ]
        out = sv(state, trace, {"decision": {"enabled": True, "on_fail": "rules"}})
        assert out["act"] is False

    def test_borderline_needs_repeat_confirmation(self):
        # chain_candidates=1 + gap 8 fires miss_chain at p=0.68 — above
        # the 0.62 threshold it ACTS; the borderline path is exercised by
        # simulating a sub-threshold raw answer instead (timeline confirm)
        state = {"current_iteration": 20, "findings": [], "_verdict_timeline": []}
        trace = [{"tool_name": "http_request", "args": "a", "success": True}]
        out = sv(state, trace, {"decision": {"enabled": True, "on_fail": "rules"}})
        # healthy-ish single reading → no act
        assert out["act"] is False
        # borderline confirmation: a remote answering sub-threshold drift
        # twice in a row (same kind, ≤3 turns apart) must ACT on the second
        import json as _j

        def _drift_remote(ep, payload, timeout):
            return _j.dumps({"qset": 1, "answers": [{"qid": "supervisor.verdict", "choice": "drift", "p": 0.6}]})

        from suijin.modules.agent.lib.reflex.core.client import DecisionClient

        cfg = {"decision": {"enabled": True, "engine": "http"}}  # inject via the http transport
        state2 = {"current_iteration": 20, "findings": [], "_verdict_timeline": []}
        trace2 = [{"tool_name": "http_request", "args": "a", "success": True}]
        # monkey the client used by verdict()
        import suijin.modules.agent.lib.reflex.core.client as clmod

        _orig = clmod.decide
        clmod.decide = lambda qid, feats, config=None: DecisionClient(cfg, transport=_drift_remote).decide(qid, feats)
        try:
            first = sv(state2, trace2, cfg)
            second = sv(state2, trace2, cfg)  # timeline now holds the first reading
        finally:
            clmod.decide = _orig
        assert first["act"] is False  # 0.6 < 0.62 threshold → mark only
        assert second["act"] is True and second.get("confirmed") is True

    def test_reflex_enabled_gate(self):
        assert sv_enabled({}) is False
        assert sv_enabled({"decision": {"enabled": True}}) is True


class TestGroundedPhrasing:
    def test_phrase_is_one_grounded_line(self):
        async def gen(msgs, cfg):
            assert "VERDICT: repeat" in msgs[0]["content"]  # grounded in the decision
            assert "repeat pressure" in msgs[0]["content"]
            return "OFFER: three near-identical calls in a row — change the argument or move on."

        out = asyncio.run(sv_phrase({"kind": "repeat", "p": 0.9, "features": {"repeat_pressure": 0.8}}, gen, {}))
        assert out and out.startswith("OFFER:")

    def test_phrase_refusal_is_silence(self):
        async def gen(msgs, cfg):
            return "NO_COMMENT"

        assert asyncio.run(sv_phrase({"kind": "drift", "p": 0.9, "features": {}}, gen, {})) is None


# ── drifter ──────────────────────────────────────────────────────────────


class TestDrifter:
    def test_alignment_writes_timeline_and_score(self):
        state = {}
        out = align(
            state,
            [{"tool_name": "search_kb", "args": "q", "success": True}],
            {"decision": {"enabled": True, "on_fail": "rules"}},
        )
        assert "_drift" in state and "_drift_score" in state
        assert out["kind"] in ("none", "on_course")  # early turns: not enough signal

    def test_sustained_drift_needs_three_of_four(self):
        state = {
            "_drift": [
                {"turn": 1, "kind": "drifting", "p": 0.7},
                {"turn": 2, "kind": "drifting", "p": 0.7},
                {"turn": 3, "kind": "drifting", "p": 0.7},
            ]
        }
        trace = [{"tool_name": "web_search", "args": f"q{i}", "success": True} for i in range(6)]
        out = align(state, trace, {"decision": {"enabled": True, "on_fail": "rules"}})
        assert out["sustained"] is True and out["score"] >= 0.6


# ── oracle ───────────────────────────────────────────────────────────────


class TestOracle:
    def test_clean_output_is_none(self):
        out = triage("{'ok': true, 'items': [1,2]}", 200, 30, {"decision": {"enabled": True, "on_fail": "rules"}})
        assert out["class"] == "none"

    def test_error_soup_is_anomaly(self):
        out = triage(
            "Traceback (most recent call last): Exception: FATAL error in syntax",
            500,
            30,
            {"decision": {"enabled": True, "on_fail": "rules"}},
        )
        assert out["class"].startswith("anomaly")

    def test_deny_high_conf_distills_to_cheatsheet(self, tmp_path, monkeypatch):
        from suijin.modules.agent.lib import cheatsheet as cs

        monkeypatch.setattr(cs, "_path", lambda: tmp_path / "cheat.json")
        cs.reset_cache()
        out = adjudicate(
            {"class": "sqli", "evidence": []},
            control_difference=0.0,
            config={"decision": {"enabled": True, "on_fail": "rules"}},
        )
        assert out["verdict"] == "deny"
        assert any(s["tag"] == "oracle-deny" for s in cs.snippets())


# ── shadow ───────────────────────────────────────────────────────────────


class TestShadow:
    def test_log_and_precision(self, shadow_file):
        shadow.shadow_log(
            "supervisor.verdict",
            {"iteration": 5, "phase": "recon"},
            {"choice": "stall", "p": 0.8, "engine": "local"},
            legacy="coach text",
            acted=True,
            turn=5,
        )
        assert shadow.intervention_precision() is None  # no outcome yet — honest None
        shadow.record_outcome(8, improved=True)
        assert shadow.intervention_precision() == 1.0

    def test_unacted_records_do_not_count(self, shadow_file):
        shadow.shadow_log("supervisor.verdict", {"iteration": 5}, None, legacy=None, acted=False, turn=5)
        shadow.record_outcome(8, improved=False)
        assert shadow.intervention_precision() is None  # only ACTED records measure precision


class TestEngines:
    def test_default_engine_is_laya_mlx(self):
        from suijin.modules.agent.lib.reflex.core.client import DEFAULTS, decision_config

        assert DEFAULTS["engine"] == "laya-mlx"  # the operator's System One model
        assert decision_config({"decision": {"enabled": True}})["engine"] == "laya-mlx"

    def test_laya_engine_routes_in_process(self):
        # model_path → the real Agent (this machine has the checkpoint)
        from pathlib import Path

        from suijin.modules.agent.lib.reflex.core.client import DecisionClient, laya_status

        if not Path("~/laya-mlx").expanduser().is_dir():
            pytest.skip("no local laya checkpoint")
        assert laya_status("~/laya-mlx") == "loaded"
        c = DecisionClient({"decision": {"enabled": True, "engine": "laya-mlx", "model_path": "~/laya-mlx"}})
        f = {
            "phase": "recon",
            "iteration": 5,
            "iterations_since_finding": 1,
            "repeat_pressure": 0.05,
            "tool_fail_cluster": 0,
            "phase_tool_mismatch": 0,
            "chain_candidates": 0,
            "drift_score": 0.0,
            "unheeded_count": 0,
        }
        out = c.decide("supervisor.verdict", f)
        assert out and out["engine"] == "laya-mlx"  # the real model answered
        assert out["ms"] < 1000  # ms-class, not generation-class

    def test_missing_model_falls_to_local(self):
        from suijin.modules.agent.lib.reflex.core.client import DecisionClient

        c = DecisionClient({"decision": {"enabled": True, "engine": "laya-mlx", "model_path": "/nonexistent/model"}})
        out = c.decide("supervisor.verdict", {"repeat_pressure": 0.8})
        assert out["engine"] == "local"  # ladder held: never a crash, never a guess

    def test_question_laya_format(self):
        from suijin.modules.agent.lib.reflex.core.questions import get_question

        laya_q = get_question("supervisor.verdict").laya()
        assert laya_q["type"] == "choice"
        assert set(laya_q["criteria"]) == {"none", "drift", "stall", "repeat", "miss_chain"}
        assert all(isinstance(v, str) and v for v in laya_q["criteria"].values())  # definitions, not bare labels

    def test_settings_bridge_roundtrip(self):
        from suijin.modules.console.lib.settings_tui import _decision_pack, _decision_unpack

        cfg = {"decision": {"enabled": True, "engine": "laya-mlx"}}
        _decision_unpack(cfg)
        assert cfg["decision_enabled"] is True
        cfg["decision_model_path"] = "~/laya-mlx"
        _decision_pack(cfg)
        d = cfg["decision"]
        assert d["enabled"] is True and d["model_path"] == "~/laya-mlx"
        assert d["on_fail"] == "local"  # pack fills sane defaults
        assert "decision_enabled" not in cfg
