"""Run-loop fuzz harness — "one mistake must never break the engagement."

The structural-bug class this locks out: the provider-restart latch that
continued the wrong loop (restart was fiction), the unreachable while/else
(stale resume = false no-output death), KI-at-the-wrong-frame ending the
run instead of pausing, malformed events killing the loop, and unclamped
model-authored waits freezing the pipeline. Invariant for EVERY case:
run_red_team_async RETURNS — classified termination or clean completion —
never an unhandled exception, never a silent vanish.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import suijin.modules.redteam.lib.redteamer as rt


class FakeGraph:
    """Scripted astream with call instrumentation."""

    def __init__(self, agent, streams):
        self._agent = agent
        self.streams = list(streams)  # each: async fn(ig) -> events list
        self.astream_calls = []
        self.updated_states = []

    async def astream(self, input_state, config):
        self.astream_calls.append(input_state)
        idx = min(len(self.astream_calls) - 1, len(self.streams) - 1)
        fn = self.streams[idx]
        for ev in await fn(self):
            yield ev

    def update_state(self, config, values):
        self.updated_states.append(values)

    def get_state(self, thread_id):
        return self._agent.state or {}


class FakeAgent:
    def __init__(self, streams, state=None):
        self._graph = FakeGraph(self, streams)
        self.state = state or {}
        self.built = False

    def _build(self):
        self.built = True

    def get_state(self, thread_id):
        return self.state


def _ok_events(reason="objective_complete"):
    trace = [
        {"iteration": 1, "thought": "t", "tool_name": "nmap_scan", "tool_args": {}, "success": True, "phase": "recon"}
    ]
    return [
        {"think": {"execution_trace": trace, "_current_step": {}, "current_phase": "recon"}},
        {
            "generate_response": {
                "execution_trace": trace,
                "current_phase": "recon",
                "completion_reason": reason,
                "messages": [{"role": "assistant", "content": "done"}],
            }
        },
    ]


async def _provider_flake(ig):
    return [{"generate_response": {"completion_reason": "provider_failure", "current_phase": "recon", "messages": []}}]


async def _ok_stream(ig):
    return _ok_events()


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Hermetic loop run: real run_red_team_async, fake graph, no UI/IO side effects."""
    from suijin.modules.platform.lib import workspace as _ws

    monkeypatch.setattr(_ws, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(rt, "DUMP_PATH", Path(str(tmp_path)) / "recovery.json")
    # non-TTY: pause console + asks resolve instantly, no stdin fights
    monkeypatch.setattr(rt.sc, "pause_console", lambda ctx, input_fn: "")
    monkeypatch.setattr(rt, "_operator_input", lambda label, timeout_s=600.0: "test answer", raising=False)
    # the non-TTY ask path routes through the desktop/gateway bridge —
    # answer instantly instead of polling a bridge that does not exist
    from suijin.modules.console.lib import gateway as _gw

    monkeypatch.setattr(_gw, "push_question", lambda q: "qid-test")
    monkeypatch.setattr(_gw, "fetch_answer", lambda qid, timeout_s=600.0: "gateway answer")

    def _mk(agent):
        monkeypatch.setattr(rt, "SuijinAgentGraph", lambda **kw: agent)
        return agent

    return _mk


def _run():
    return asyncio.run(rt.run_red_team_async({"provider": "deepseek", "max_iterations": 5}, "fuzz target"))


class TestRestartLatch:
    def test_provider_failure_actually_restarts(self, isolated):
        """THE structural bug: the old `continue` targeted the inner loop —
        the rebuilt graph was never streamed and the run died anyway. Now:
        the second graph IS streamed (with carried state) and completes."""
        first = FakeAgent([_provider_flake])
        second = FakeAgent([_ok_stream])
        box = {"n": 0}

        def factory(**kw):
            box["n"] += 1
            return first if box["n"] == 1 else second

        import suijin.modules.redteam.lib.redteamer as rt2

        orig = rt2.SuijinAgentGraph
        rt2.SuijinAgentGraph = factory
        try:
            out = _run()
        finally:
            rt2.SuijinAgentGraph = orig
        assert second.built, "the replacement graph was never built"
        assert len(second._graph.astream_calls) == 1, "the replacement graph was never streamed (restart is fiction)"
        assert out.get("completion_reason") == "objective_complete"

    def test_second_provider_failure_ends_cleanly(self, isolated):
        """One restart, then a clean stop — never an infinite flake loop."""
        first = FakeAgent([_provider_flake])
        second = FakeAgent([_provider_flake])
        box = {"n": 0}

        def factory(**kw):
            box["n"] += 1
            return first if box["n"] == 1 else second

        orig = rt.SuijinAgentGraph
        rt.SuijinAgentGraph = factory
        try:
            out = _run()
        finally:
            rt.SuijinAgentGraph = orig
        assert out.get("completion_reason") == "provider_failure"


class TestMalformedEvents:
    def test_event_soup_degrades_to_skips(self, isolated):
        async def soup(ig):
            return [
                "just a string",  # non-dict
                {},  # empty
                {"think": "output-is-not-a-dict"},  # bad node output
                None if False else {"think": {"execution_trace": [], "_current_step": {}, "current_phase": "recon"}},
                *_ok_events(),
            ]

        agent = FakeAgent([soup])
        isolated(agent)
        out = _run()  # must not raise
        assert out.get("completion_reason") == "objective_complete"

    def test_stream_that_raises_is_reported_not_raised(self, isolated):
        async def boom(ig):
            raise RuntimeError("langgraph internals changed")
            yield  # pragma: no cover

        agent = FakeAgent([boom])
        isolated(agent)
        out = _run()  # crash panel path — returns, never raises
        assert out is not None


class TestInterruptSemantics:
    def test_ki_inside_graph_pauses_not_ends(self, isolated):
        """Ctrl+C landing mid-node: the reader swallows the KI and the
        sentinel arrives — the loop must PAUSE (resume + re-stream), not
        end with a false no-output diagnosis."""

        async def interrupted_then_ok(ig):
            import signal as _sig

            if len(ig.astream_calls) == 1:
                _sig._suijin_interrupted = True  # KI landed inside the graph
                return []  # sentinel immediately
            _sig._suijin_interrupted = False
            return _ok_events()

        agent = FakeAgent([interrupted_then_ok])
        isolated(agent)
        out = _run()
        # the loop resumed and completed — NOT a silent empty ending
        assert out.get("completion_reason") == "objective_complete"
        assert len(agent._graph.astream_calls) >= 2, "the loop never resumed after the interrupt"


class TestAskFlow:
    def test_ask_holds_the_graph_and_injects(self, isolated, monkeypatch):
        """ask_operator = real pause: the stream is held (cancelled), the
        answer is injected via update_state, and the graph re-streams from
        its checkpoint with the answer present."""
        injected = []

        class RecGraph(FakeGraph):
            def update_state(self, config, values):
                injected.append(values)
                super().update_state(config, values)

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

        async def ask_stream(ig):
            return [
                {
                    "execute_tool": {
                        "execution_trace": trace,
                        "_current_step": {
                            "tool_output": "should I proceed?",
                            "error_class": "ask_operator",
                            "tool_name": "ask_operator",
                        },
                        "current_phase": "recon",
                    }
                },
            ]

        async def done_stream(ig):
            # stream 2: the checkpoint is PAST the ask — plain completion
            # (a real graph never replays the ask; the fake must not either)
            tr2 = [dict(trace[0], tool_name="nmap_scan")]
            return [
                {"think": {"execution_trace": tr2, "_current_step": {}, "current_phase": "recon"}},
                *_ok_events(),
            ]

        agent = FakeAgent([ask_stream, done_stream])
        agent._graph.__class__ = RecGraph
        isolated(agent)
        out = _run()
        assert out.get("completion_reason") == "objective_complete"
        assert any("OPERATOR ANSWER" in json.dumps(v) for v in injected), "the answer never reached the graph"
        assert len(agent._graph.astream_calls) >= 2, "the graph was never re-streamed after the ask hold"


class TestModelAuthoredWaits:
    def test_job_wait_timeout_is_clamped(self):
        """Model-authored job_wait timeout=3600 froze the whole pipeline —
        the clamp bounds it to 120s and the wait runs OFF the event loop."""
        import suijin.modules.agent.lib.nodes.execute_tool_node as etn

        seen = {}

        def fake_route(name, args, cfg):
            seen.update(args)
            return "done waiting"

        out = asyncio.run(
            etn.execute_tool_node(
                {
                    "_current_step": {
                        "tool_name": "job_wait",
                        "tool_args": {"job_id": "j", "timeout": 3600},
                        "iteration": 3,
                    },
                    "current_phase": "exploitation",
                },
                route_tool_fn=fake_route,
            )
        )
        assert seen["timeout"] <= 120, seen
        assert "AUTO-BG" not in out["_current_step"]["tool_output"]


class TestSeededFuzz:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_random_event_storms_always_return(self, isolated, seed):
        """Seeded random event sequences (valid + malformed + flakes +
        interrupts) — the invariant: run_red_team_async RETURNS, always."""

        import random

        rng = random.Random(seed)

        async def storm(ig):
            evs = []
            for _ in range(rng.randint(1, 6)):
                kind = rng.choice(["ok", "garbage", "empty_dict", "flake", "interrupt_flag"])
                if kind == "ok":
                    evs.append({"think": {"execution_trace": [], "_current_step": {}, "current_phase": "recon"}})
                elif kind == "garbage":
                    evs.append(rng.choice(["str", 42, ["list"]]))
                elif kind == "empty_dict":
                    evs.append({})
                elif kind == "flake":
                    return [
                        {
                            "generate_response": {
                                "completion_reason": rng.choice(["provider_failure", "llm_error"]),
                                "current_phase": "recon",
                                "messages": [],
                            }
                        }
                    ]
                else:
                    import signal as _sig

                    _sig._suijin_interrupted = True
                    return []
            evs.extend(_ok_events())
            return evs

        agent = FakeAgent([storm, storm, _ok_stream])
        isolated(agent)
        import signal as _sig

        _sig._suijin_interrupted = False
        try:
            out = _run()  # NEVER raises
            assert out is not None
        finally:
            _sig._suijin_interrupted = False
