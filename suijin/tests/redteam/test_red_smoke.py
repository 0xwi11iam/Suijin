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
        {"think": {"execution_trace": trace1, "_current_step": {}, "current_phase": "informational"}},
        {
            "execute_tool": {
                "execution_trace": trace1,
                "_current_step": {"tool_output": "22/tcp open", "error_class": ""},
                "current_phase": "informational",
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
    monkeypatch.setattr(rt, "DUMP_PATH", Path(str(tmp_path)) / "recovery.json")
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

    def test_state_dump_written(self, red_mocks):
        _run_smoke()
        dump = Path(red_mocks["tmpdir"]) / "recovery.json"
        assert dump.exists()
        data = __import__("json").loads(dump.read_text())
        assert data["objective"] == "test target"

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
