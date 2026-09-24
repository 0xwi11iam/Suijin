"""Autonomy mode (full) — unattended CI runs, zero human interaction.

The unattended-run contract: with autonomy=full the engagement never
stalls on a human — asks auto-answer (and are logged as hypotheses via
the questions file protocol), the order tells the model it's unattended,
SIGTERM (docker stop) triggers the full-save path, and press-Enter
prompts are skipped when nobody can press.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import suijin.modules.redteam.lib.redteamer as rt


class FakeGraph:
    def __init__(self, agent, streams):
        self.a, self.streams, self.calls, self.upd = agent, streams, [], []

    async def astream(self, input_state, config):
        self.calls.append(input_state)
        fn = self.streams[min(len(self.calls) - 1, len(self.streams) - 1)]
        for ev in await fn(self):
            yield ev

    def update_state(self, config, values):
        self.upd.append(values)

    def get_state(self, thread_id):
        return self.a.state or {}


class FakeAgent:
    def __init__(self, streams, state=None):
        self._graph, self.state, self.built = FakeGraph(self, streams), state or {}, False

    def _build(self):
        self.built = True

    def get_state(self, t):
        return self.state


def _ok_events(reason="objective_complete"):
    tr = [
        {"iteration": 1, "thought": "t", "tool_name": "nmap_scan", "tool_args": {}, "success": True, "phase": "recon"}
    ]
    return [
        {"think": {"execution_trace": tr, "_current_step": {}, "current_phase": "recon"}},
        {
            "generate_response": {
                "execution_trace": tr,
                "current_phase": "recon",
                "completion_reason": reason,
                "messages": [{"role": "assistant", "content": "done"}],
            }
        },
    ]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    from suijin.modules.platform.lib import workspace as _ws

    monkeypatch.setattr(_ws, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(rt.sc, "pause_console", lambda ctx, input_fn: "")
    return tmp_path


def _run(config):
    return asyncio.run(rt.run_red_team_async(config, "autonomy test target"))


class TestAutonomyAskPolicy:
    def test_ask_auto_answers_without_stalling(self, isolated, monkeypatch):
        """autonomy=full: an ask NEVER touches the gateway/human path — it
        injects the unattended answer immediately and logs the question."""
        stalled = []
        from suijin.modules.console.lib import gateway as _gw

        monkeypatch.setattr(_gw, "fetch_answer", lambda qid, timeout_s=600.0: stalled.append(qid) or "LATE")
        pushed = []
        monkeypatch.setattr(_gw, "push_question", lambda q: pushed.append(q) or "qid-1")

        trace = [
            {
                "iteration": 1,
                "thought": "t",
                "tool_name": "ask_operator",
                "tool_args": {},
                "success": True,
                "phase": "recon",
            }
        ]

        async def ask_then_done(ig):
            # FULL-AUTO never holds the stream: the ask is answered inline
            # (via update_state) and the SAME astream continues to completion
            return [
                {
                    "execute_tool": {
                        "execution_trace": trace,
                        "_current_step": {
                            "tool_output": "should I poke /admin?",
                            "error_class": "ask_operator",
                            "tool_name": "ask_operator",
                        },
                        "current_phase": "recon",
                    }
                },
                *_ok_events(),
            ]

        agent = FakeAgent([ask_then_done])
        monkeypatch.setattr(rt, "SuijinAgentGraph", lambda **kw: agent)
        out = _run({"provider": "deepseek", "max_iterations": 5, "autonomy": "full"})
        assert out.get("completion_reason") == "objective_complete"
        assert stalled == [], "the gateway HUMAN path was touched in full-auto"
        assert pushed == ["should I poke /admin?"]  # logged for the result doc
        injected = json.dumps(agent._graph.upd)
        assert "OPERATOR (unattended)" in injected and "best" in injected

    def test_interactive_mode_untouched(self):
        """autonomy absent: the config validates to '' — interactive runs
        keep every behavior (the flag is purely additive)."""
        from suijin.modules.platform.lib.config_models import RedConfig

        c = RedConfig(provider="zai")
        assert c.autonomy == ""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RedConfig(provider="zai", autonomy="sometimes")


class TestUnattendedDoctrine:
    def test_order_carries_unattended_directive(self):
        from suijin.modules.agent.lib.nodes.think_node import think_node

        seen = []

        async def gen(messages, config=None, **kw):
            seen.append(messages)
            return '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}'

        st = {
            "objective": "o",
            "original_objective": "pentest http://t.local",
            "target_info": {},
            "messages": [],
            "current_iteration": 1,
            "_run_config": {"autonomy": "full"},
        }
        asyncio.run(think_node(st, generate_fn=gen, config={}))
        user_turn = next(m["content"] for m in seen[0] if m["role"] == "user")
        assert "UNATTENDED MODE" in user_turn and "Do NOT ask_operator" in user_turn

    def test_interactive_order_has_no_directive(self):
        from suijin.modules.agent.lib.nodes.think_node import think_node

        seen = []

        async def gen(messages, config=None, **kw):
            seen.append(messages)
            return '{"action":"use_tool","tool_name":"search_kb","tool_args":{"keyword":"x"},"thought":"t"}'

        st = {
            "objective": "o",
            "original_objective": "pentest http://t.local",
            "target_info": {},
            "messages": [],
            "current_iteration": 1,
        }
        asyncio.run(think_node(st, generate_fn=gen, config={}))
        user_turn = next(m["content"] for m in seen[0] if m["role"] == "user")
        assert "UNATTENDED MODE" not in user_turn


class TestSigtermSave:
    def test_sigterm_handler_installed_in_full_auto(self):
        import signal as _sig

        old = _sig.getsignal(_sig.SIGTERM)
        try:
            rt._install_sigterm_save()
            assert callable(_sig.getsignal(_sig.SIGTERM))
            assert _sig.getsignal(_sig.SIGTERM) is not old
        finally:
            _sig.signal(_sig.SIGTERM, old if old is not None else _sig.SIG_DFL)

    def test_unattended_detection(self, monkeypatch):
        import io

        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert rt._unattended() is True


class TestAdjustConfigGuard:
    def test_autonomy_is_operator_only(self):
        from suijin.modules.tools.lib.self_config import FORBIDDEN

        assert "autonomy" in FORBIDDEN
