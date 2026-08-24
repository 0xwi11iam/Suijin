"""BF2 — the org chart: primary loop prompts, watcher fleet, responders.

Proof: scripted mock-LLM episodes drive REAL blue tool calls whose
effects are observable (enforcement plane); the blue primary renders
blue doctrine (never red); blue-mode fireteams route BLUE tools.
"""

import asyncio
import json

import pytest


@pytest.fixture(autouse=True)
def _plane(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUE_ENFORCEMENT_FILE", str(tmp_path / "enf.json"))
    import suijin.modules.blueteam.lib.blue.enforcement as enf

    enf._DEFAULT_PATH = None  # re-resolve
    yield enf


class TestBluePrompts:
    def test_doctrine_and_tools_render(self):
        from suijin.modules.blueteam.lib.blue.agent import blue_system_prompt

        p = blue_system_prompt({})
        assert "Autonomous Defense Agent" in p
        assert "blue_block" in p and "blue_honeypot" in p and "blue_shell" in p
        assert "DEFENSIVE PLAYBOOKS" in p and "exfil_suspicion" in p
        # NO red doctrine leaks into the blue prompt
        assert "Offensive Security Agent" not in p and "engagement order" not in p.lower()

    def test_defensive_order_shape(self):
        from suijin.modules.blueteam.lib.blue.agent import defensive_order

        o = defensive_order("defend the hill")
        assert "DEFENSE SHIFT" in o and "defend the hill" in o

    def test_blue_mode_think_renders_blue(self):
        """The injection point: state['_blue_mode'] makes think_node build
        the BLUE prompt and the defensive order — the graph scaffolding,
        decision parsing, and H-wave context blocks all carry over."""
        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[-1]["content"]
            return json.dumps({"action": "complete", "completion_reason": "post maintained", "thought": "t"})

        asyncio.run(
            tn.think_node(
                {
                    "messages": [],
                    "execution_trace": [],
                    "current_iteration": 1,
                    "current_phase": "informational",
                    "original_objective": "hold the hill",
                    "todo_list": [],
                    "_blue_mode": True,
                },
                generate_fn=gen,
            )
        )
        assert "Autonomous Defense Agent" in captured["system"]
        assert "blue_block" in captured["system"]  # the arsenal is taught
        assert "DEFENSE SHIFT" in captured["user"] and "hold the hill" in captured["user"]
        assert "CONTRACTED ENGAGEMENT" not in captured["user"]  # red order never appears

    def test_red_mode_unchanged(self):
        from suijin.modules.agent.lib.nodes import think_node as tn

        captured = {}

        async def gen(messages, config=None, **kw):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[-1]["content"]
            return json.dumps({"action": "complete", "completion_reason": "done", "thought": "t"})

        asyncio.run(
            tn.think_node(
                {
                    "messages": [],
                    "execution_trace": [],
                    "current_iteration": 1,
                    "current_phase": "informational",
                    "original_objective": "attack x",
                    "todo_list": [],
                },
                generate_fn=gen,
            )
        )
        assert "Offensive Security Agent" in captured["system"]
        assert "DEFENSE SHIFT" not in captured["user"]


class TestPrimaryLoop:
    def test_primary_runs_blue_tools(self, _plane):
        """The primary graph: a scripted decision calls blue_block through
        the REAL dispatch; the effect lands on the enforcement plane."""
        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        script = [
            {
                "action": "use_tool",
                "tool_name": "blue_block",
                "tool_args": {"ip": "10.66.66.66", "reason": "canary hit"},
                "thought": "stolen material in use",
            },
            {"action": "complete", "completion_reason": "contained", "thought": "done"},
        ]

        async def gen(messages, config=None, **kw):
            return json.dumps(script.pop(0))

        graph = SuijinAgentGraph(generate_fn=gen, route_tool_fn=route_blue_tool, max_iterations=3)
        state = asyncio.run(graph.run("defend the hill", thread_id="bf2-primary-1"))
        assert _plane.is_blocked("10.66.66.66")  # the action LANDED
        assert state.get("current_iteration", 0) >= 1


class TestWatchers:
    def test_detection_and_auto_enforcement(self, _plane):
        from suijin.modules.blueteam.lib.blue.watchers import EndpointWatcher, apply_fast_path, watcher_report

        w = EndpointWatcher({"path": "/hill/login"})
        findings = []
        for _ in range(6):
            findings += w.check(
                {
                    "path": "/hill/login",
                    "body": "user=x&password=bad",
                    "ip": "10.5.5.5",
                    "user_agent": "u",
                    "headers": {},
                    "query": "",
                }
            )
        vel = [f for f in findings if f["signal"] == "auth_fail_velocity"]
        assert vel and vel[0]["fast_path"]  # criticality flagged
        apply_fast_path(vel[0])
        assert _plane.is_blocked("10.5.5.5")  # enforced without any LLM
        rep = watcher_report(findings)
        assert "auth_fail_velocity" in rep and "auto-enforced" in rep

    def test_typed_critical_events_fast_path(self):
        from suijin.modules.blueteam.lib.blue.watchers import EndpointWatcher

        w = EndpointWatcher({"path": "/vault/blob"})
        f = w.check_event({"type": "vault_access", "path": "/vault/blob", "ip": "10.6.6.6"})
        assert f and f[0]["fast_path"] and f[0]["weight"] == 5

    def test_ownership_boundaries(self):
        from suijin.modules.blueteam.lib.blue.watchers import EndpointWatcher

        w = EndpointWatcher({"path": "/api/users/<int:uid>"})
        assert w._owns("/api/users/42")
        assert not w._owns("/api/users_export")

    def test_fleet_seeded_from_analysis(self):
        from suijin.modules.blueteam.lib.blue.watchers import spawn_from_analysis

        class FakeSA:
            risk_score = 8
            normal_patterns = ["GET"]

        fleet = spawn_from_analysis([{"path": "/vault/blob"}, {"path": "/x"}], {"/vault/blob": FakeSA()})
        by_path = {w.path: w for w in fleet}
        assert by_path["/vault/blob"].risk == 8  # analysis finally feeds the fleet
        assert by_path["/vault/blob"].normal_patterns == ["GET"]

    def test_benign_traffic_silent(self):
        from suijin.modules.blueteam.lib.blue.watchers import EndpointWatcher

        w = EndpointWatcher({"path": "/hill/api/docs"})
        f = w.check(
            {
                "path": "/hill/api/docs",
                "body": "",
                "ip": "1.1.1.1",
                "user_agent": "Mozilla/5.0",
                "headers": {},
                "query": "",
            }
        )
        assert f == []


class TestResponders:
    def test_blue_fireteam_routes_blue_tools(self, _plane):
        """Coupling fix proven: a fireteam spawned under a blue router makes
        its specialists call BLUE tools (not red dispatch)."""
        from suijin.modules.agent.lib.nodes.subagent_node import deploy_fireteam
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        async def gen(messages, config=None, **kw):
            sys = messages[0]["content"]
            if "blue_block" not in sys:
                # the specialist's prompt must advertise the blue arsenal
                raise AssertionError("responder prompt lacks blue tools")
            return json.dumps(
                {
                    "action": "use_tool",
                    "tool_name": "blue_block",
                    "tool_args": {"ip": "10.77.77.77", "reason": "responder sweep"},
                    "thought": "blocking",
                }
            )

        async def _run():
            from suijin.modules.agent.lib.nodes.subagent_node import collect_finished_teams

            dep = deploy_fireteam(
                ["block 10.77.77.77 — responder sweep task"], generate_fn=gen, route_tool_fn=route_blue_tool
            )
            # hold THIS loop open until the specialist finishes — the team
            # runs as asyncio tasks and dies with the loop (asyncio.run
            # returning early orphaned them; why this raced local-vs-CI)
            import time as _t

            deadline = _t.time() + 45
            while _t.time() < deadline:
                msgs = collect_finished_teams()
                if msgs:
                    break
                await asyncio.sleep(0.2)
            return dep

        dep = asyncio.run(_run())
        assert dep.get("team_id")
        # the episode completed inside the live loop; the block must have landed
        assert _plane.is_blocked("10.77.77.77"), f"responder never blocked; team={dep}"

    def test_event_queue_roundtrip(self):
        from suijin.modules.blueteam.lib.blue import agent as ba

        ba.queue_event("WATCHER /vault: vault_access from 10.9.9.9 [auto-enforced]")
        drained = ba.drain_events()
        assert drained and "vault_access" in drained[0]
        assert ba.drain_events() == []  # exactly once
