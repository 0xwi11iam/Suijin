"""Red team smoke test — run_red_team_async with a stubbed agent graph.

Verifies the full agent loop without LLM or real tools: fake graph events
flow through iteration display, audit logging, completion detection, and
the final report + session save.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import suijin.modules.redteam.lib.redteamer as rt


class FakeGraph:
    """Fake LangGraph object with scripted astream events."""

    def __init__(self, events):
        self._events = list(events)
        self.updated_states = []

    async def astream(self, input_state, config):
        for event in self._events:
            yield event

    def update_state(self, config, values):
        self.updated_states.append(values)

    def get_state(self, thread_id):
        return {}


class FakeAgent:
    def __init__(self, events):
        self._graph = FakeGraph(events)
        self.built = False
        self.get_state_calls = []

    def _build(self):
        self.built = True

    def get_state(self, thread_id):
        self.get_state_calls.append(thread_id)
        return {}


def _happy_events():
    """Scripted events: think -> execute_tool -> generate_response (complete)."""
    trace1 = [
        {
            "iteration": 1,
            "thought": "Scan the target",
            "tool_name": "nmap_scan",
            "tool_args": {"target": "x"},
            "reasoning": "start recon",
            "success": True,
            "phase": "informational",
        }
    ]
    return [
        {
            "think": {
                "execution_trace": trace1,
                "_current_step": {},
                "current_phase": "informational",
                "current_iteration": 1,
                "todo_list": [{"task": "scan"}],
                "findings": [],
                "target_info": {"host": "x"},
            }
        },
        {
            "execute_tool": {
                "execution_trace": trace1,
                "_current_step": {"tool_output": "22/tcp open", "error_class": ""},
                "current_phase": "informational",
                "current_iteration": 1,
            }
        },
        {
            "generate_response": {
                "execution_trace": trace1,
                "current_phase": "informational",
                "completion_reason": "objective_complete",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Engagement complete. Found open ports and documented findings in the final report.",
                    }
                ],
            }
        },
    ]


@pytest.fixture
def red_mocks(monkeypatch, tmp_path):
    """Mock the agent graph + isolate state dump file + crash logs (tests
    used to pollute the operator's real outputs/logs/engage_crash.log)."""
    fake_agent = FakeAgent(_happy_events())
    monkeypatch.setattr(rt, "SuijinAgentGraph", lambda **kwargs: fake_agent)
    from suijin.modules.platform.lib import workspace as _ws

    monkeypatch.setattr(_ws, "WORKSPACE_DIR", tmp_path)
    return {"agent": fake_agent, "tmpdir": str(tmp_path)}


def _run_smoke(config=None):
    config = config or {
        "provider": "deepseek",
        "final_model_id": "deepseek-v4-flash",
        "max_iterations": 5,
    }
    return asyncio.run(rt.run_red_team_async(config, "test target"))


class TestRedTeamSmoke:
    def test_happy_path_completes(self, red_mocks):
        _run_smoke()
        assert red_mocks["agent"].built is True

    def test_proxy_config_applied(self, red_mocks, monkeypatch):
        """proxy_url in config -> set_proxy called with it."""
        seen = {}
        monkeypatch.setattr(rt._dispatch, "set_proxy", lambda url: seen.update(url=url))
        _run_smoke({"provider": "deepseek", "max_iterations": 5, "proxy_url": "http://proxy.local:8080"})
        assert seen.get("url") == "http://proxy.local:8080"

    def test_usage_reset_at_start(self, red_mocks, monkeypatch):
        seen = []
        monkeypatch.setattr(rt.providers, "reset_usage", lambda: seen.append(True))
        _run_smoke()
        assert seen == [True]

    def test_bus_registered_and_cleared(self, red_mocks, monkeypatch):
        """The runner announces its live bus on start and clears it on
        end — the gateway live-stream watches that registry."""
        import suijin.server

        registered = {}
        real_reg = suijin.server.register_active_bus

        def _reg(bus):
            registered["bus"] = bus
            real_reg(bus)

        monkeypatch.setattr("suijin.server.register_active_bus", _reg)
        _run_smoke()
        assert isinstance(registered.get("bus"), suijin.server.EventBus)
        from suijin.server import active_bus

        assert active_bus() is None  # cleared by the finally

    def test_journal_written_and_replayable(self, red_mocks):
        """The run appends the durable journal (iteration + snapshot +
        complete) and replay() reconstructs a resume-able state."""
        _run_smoke()
        logs = list((Path(red_mocks["tmpdir"]) / "engagements").glob("*/events.jsonl"))
        assert logs, "runner wrote no events.jsonl"
        from suijin.modules.agent.lib.event_log import read_events, replay
        from suijin.modules.ops.lib.journal_resume import _is_resumable

        evs = read_events(logs[0])
        kinds = {e["kind"] for e in evs}
        assert "session.start" in kinds and "iteration" in kinds
        assert "state.snapshot" in kinds  # resume-critical fields persisted
        assert "session.complete" in kinds
        st = replay(logs[0])
        assert st["original_objective"] == "test target"
        assert st["current_iteration"] >= 1
        assert isinstance(st["execution_trace"], list)
        # a completed run must NOT present itself as resumable
        assert not _is_resumable(logs[0])

    def test_conclusion_bundle_derived_from_the_journal(self, red_mocks):
        """The run's conclusion .sje is a DERIVED export: its graph_state
        is the journal's replay, not a copy of a live frame — the bundle
        can never contradict events.jsonl."""
        import json
        import zipfile

        _run_smoke()
        from suijin.modules.ops.lib import sje_export
        from suijin.modules.tools.lib.engagement_bundle import CRASH_SAVER

        logs = list((Path(red_mocks["tmpdir"]) / "engagements").glob("*/events.jsonl"))
        assert logs and CRASH_SAVER.last_path is not None
        with zipfile.ZipFile(CRASH_SAVER.last_path) as zf:
            gs = json.loads(zf.read("graph_state.json"))
        want = sje_export.derive_graph_state(logs[0])
        # every journal fact reached the bundle (the stub run emits no
        # assistant messages, so compare over the keys the log actually
        # carries); completion_reason absent — resumed = run, not re-complete
        common = set(gs) & set(want)
        for k in common:
            assert gs[k] == want[k], f"{k}: bundle {gs[k]!r} != journal {want[k]!r}"
        assert gs["original_objective"] == want["original_objective"]
        assert gs["current_iteration"] >= want["current_iteration"]
        assert "completion_reason" not in gs

    def test_agent_error_path(self, red_mocks, monkeypatch):
        """A graph that raises inside astream is caught and reported."""

        class BrokenGraph:
            async def astream(self, input_state, config):
                raise RuntimeError("graph exploded")
                yield  # pragma: no cover — make it a generator

            def update_state(self, config, values):
                pass

        fake = FakeAgent([])
        fake._graph = BrokenGraph()
        monkeypatch.setattr(rt, "SuijinAgentGraph", lambda **kw: fake)
        # Must not raise out of run_red_team_async
        _run_smoke()


class TestRunRedTeamSync:
    def test_sync_wrapper(self, red_mocks, monkeypatch):
        ran = []

        async def fake_async(config, objective, api_key=None):
            ran.append(objective)

        monkeypatch.setattr(rt, "run_red_team_async", fake_async)
        rt.run_red_team({}, "sync objective")
        assert ran == ["sync objective"]
