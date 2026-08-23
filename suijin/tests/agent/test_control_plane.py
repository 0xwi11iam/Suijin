"""H4 — undormant control plane: advertised actions, queued plans, todo IDs,
and the prompt-hygiene gate (every tool the prompt teaches must exist)."""

import asyncio
import json


class TestAdvertisedActions:
    def test_decision_format_lists_all_actions(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt({})
        for action in (
            '"use_tool"',
            '"plan_tools"',
            '"switch_skill"',
            '"deploy_subagent"',
            '"ask_operator"',
            '"complete"',
        ):
            assert action in p, action

    def test_skill_library_advertised_with_examples(self):
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt

        p = build_agent_system_prompt({})
        assert "switch_skill" in p and "sql_injection" in p  # concrete examples steer usage


class TestQueuedPlan:
    def _run(self, state_over, decisions):
        """Drive think_node through a sequence of scripted LLM decisions."""
        from suijin.modules.agent.lib.nodes import think_node as tn

        state = {
            "messages": [],
            "execution_trace": [],
            "current_iteration": 0,
            "current_phase": "informational",
            "original_objective": "10.0.0.9",
            "todo_list": [],
            **state_over,
        }
        script = list(decisions)
        captured = {}

        async def gen(messages, config=None, **kw):
            captured["ctx"] = messages[0]["content"]
            return json.dumps(script.pop(0))

        outs = []
        for _ in decisions:
            out = asyncio.run(tn.think_node(state, generate_fn=gen))
            outs.append(out)
            # langgraph merge semantics: lists/messages accumulate via the
            # state's own merge; emulate by starting each turn from the
            # merged result (dict-update is what _merge_state does for
            # scalars; trace/messages we don't need here)
            state = {**state, **out}
        return outs, state, captured

    def test_plan_remaining_rendered_and_drained(self):
        plan = {
            "action": "plan_tools",
            "thought": "t",
            "tool_plan": {
                "steps": [
                    {"tool_name": "search_kb", "tool_args": {"keyword": "a"}},
                    {"tool_name": "http_request", "tool_args": {"url": "http://t/1"}},
                    {"tool_name": "http_request", "tool_args": {"url": "http://t/2"}},
                ]
            },
        }
        use1 = {"action": "use_tool", "tool_name": "http_request", "tool_args": {"url": "http://t/1"}, "thought": "t"}
        outs, state, captured = self._run({}, [plan, use1])
        # after BOTH turns: plan left 2 queued; turn-2's use_tool matched
        # the head and drained one -> 1 remains
        assert len(outs[0].get("_plan_remaining") or []) == 2  # plan turn queued 2
        assert len(state.get("_plan_remaining") or []) == 1  # head executed -> drained
        # the second turn's context rendered the queue before draining
        assert "QUEUED PLAN" in captured["ctx"] and "http_request" in captured["ctx"]

    def test_switch_skill_clears_plan(self):
        plan = {
            "action": "plan_tools",
            "thought": "t",
            "tool_plan": {
                "steps": [
                    {"tool_name": "a", "tool_args": {}},
                    {"tool_name": "b", "tool_args": {}},
                ]
            },
        }
        switch = {"action": "switch_skill", "skill_switch": {"to_skill": "sqli", "reason": "r"}, "thought": "t"}
        _, state, _ = self._run({}, [plan, switch])
        assert state["attack_path_type"] == "sqli"
        assert state.get("_plan_remaining") == []


class TestTodoIds:
    def test_todo_renders_with_id(self):
        from suijin.modules.agent.lib.state import format_todo_list

        out = format_todo_list([{"id": "t1", "description": "test login", "status": "pending", "priority": "high"}])
        assert "(id: t1)" in out


class TestPromptHygiene:
    """The audit called these tools ghosts; they turned out to be REAL pack
    tools (registered at boot, not in the pre-boot core table). This gate
    makes the classification permanent: every tool name the BACKGROUND
    section teaches must exist in the booted route table — future drift
    (a pack renamed or removed) fails this test, not a field run."""

    def test_background_section_tools_all_real(self):
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.tools.lib.dispatch import _build_routes

        routes = set(_build_routes({}).keys())
        for t in (
            "nmap_scan",
            "gobuster_dir",
            "gobuster_dns",
            "ffuf_fuzz",
            "feroxbuster_scan",
            "nikto_scan",
            "sqlmap_scan",
            "hydra_brute",
            "amass_enum",
            "execute_terminal",
        ):
            assert t in routes, f"prompt teaches {t} but it is not registered"

    def test_curated_registry_entries_all_real(self):
        from suijin.modules.agent.lib.prompts.tool_registry import _ALL_TOOLS
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.tools.lib.dispatch import _build_routes

        routes = set(_build_routes({}).keys())
        meta_only = {"ask_operator", "deploy_subagent"}  # meta-actions, not tools
        missing = sorted(_ALL_TOOLS - meta_only - routes)
        assert not missing, f"curated prompt teaches non-existent tools: {missing}"
