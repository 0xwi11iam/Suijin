"""Agent graph, state machine, and helper-contract tests.

Retargeted in v4.3: the original file silently skipped 7 tests whose
symbols were renamed during the modularisation (SuijinState -> dict
state + pydantic models, think -> think_node, classify_error ->
classify_error_class, check_guardrail -> is_hard_blocked). Skips hid
the drift; these now test the REAL surfaces and fail loudly.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestStateMachine:
    def test_state_models(self):
        from suijin.modules.agent.lib.state import ExecutionStep, TodoItem, new_agent_state

        state = new_agent_state(original_objective="test objective")
        assert state["original_objective"] == "test objective"
        item = TodoItem(description="probe the target")
        step = ExecutionStep(iteration=1, phase="informational")
        assert item.description == "probe the target"
        assert item.status == "pending" and step.iteration == 1

    def test_state_defaults(self):
        from suijin.modules.agent.lib.state import new_agent_state

        state = new_agent_state(original_objective="o")
        assert isinstance(state, dict) and state["original_objective"] == "o"
        assert state.get("current_iteration", 0) == 0

    def test_agent_graph_imports(self):
        from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

        assert SuijinAgentGraph is not None

    def test_supervisor_imports(self):
        from suijin.modules.agent.lib.supervisor import analyze_trace

        assert analyze_trace is not None


class TestThinkNode:
    def test_think_node_runs_and_parses(self):
        """think_node with a mocked LLM returning a valid decision."""
        import json as _json

        from suijin.modules.agent.lib.nodes.think_node import think_node

        async def fake_generate(messages, config=None, **kw):
            return _json.dumps(
                {"action": "use_tool", "tool_name": "search_kb", "args": {"keyword": "x"}, "thought": "t"}
            )

        state = {"objective": "o", "target_info": {}, "messages": [], "current_iteration": 1}
        out = asyncio.run(think_node(state, generate_fn=fake_generate, config={}))
        step = out.get("_current_step") or {}
        assert step.get("tool_name") == "search_kb"

    def test_think_node_parse_failure_is_data(self):
        """Unparseable LLM output must surface as parse_failure, not raise."""
        from suijin.modules.agent.lib.nodes.think_node import think_node

        async def fake_generate(messages, config=None, **kw):
            return "this is not json at all"

        state = {"objective": "o", "target_info": {}, "messages": [], "current_iteration": 1}
        out = asyncio.run(think_node(state, generate_fn=fake_generate, config={}))
        assert out.get("completion_reason") == "parse_failure"

    def test_subagent_node_imports(self):
        from suijin.modules.agent.lib.nodes.subagent_node import spawn_and_collect

        assert spawn_and_collect is not None


class TestEngagement:
    def test_session_state_imports(self):
        from suijin.modules.agent.lib.engagement import load_session_state, save_session_state

        assert save_session_state is not None
        assert load_session_state is not None


class TestErrorHandler:
    def test_error_handler_imports(self):
        from suijin.modules.platform.lib.error_handler import GracefulFallback, safe_call

        assert safe_call is not None
        assert GracefulFallback is not None

    def test_classify_error_class_contract(self):
        """The real error taxonomy entry point (was classify_error)."""
        from suijin.modules.platform.lib.helpers.error_class import classify_error_class

        out = classify_error_class(
            success=False,
            tool_output="Error: timeout",
            error_message="timeout",
            duration_ms=31000,
            tool_name="nmap_scan",
        )
        assert isinstance(out, str) and out

    def test_hard_guardrail_contract(self):
        """The real guardrail entry point (was check_guardrail)."""
        from suijin.modules.platform.lib.helpers.hard_guardrail import is_hard_blocked

        blocked, reason = is_hard_blocked("root@localhost")
        assert isinstance(blocked, bool) and isinstance(reason, str)
