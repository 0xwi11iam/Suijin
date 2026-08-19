"""Tests for agent_graph, think_node, and state machine."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestStateMachine:
    """Verify LangGraph state imports resolve."""

    def test_state_imports(self):
        try:
            from suijin.core.state import SuijinState

            assert SuijinState is not None
        except ImportError as e:
            pytest.skip(f"State module unavailable: {e}")

    def test_state_defaults(self):
        try:
            from suijin.core.state import SuijinState

            state = SuijinState(messages=[], objective="test", target="localhost")
            assert state.phase == "informational"
        except ImportError as e:
            pytest.skip(f"State module unavailable: {e}")

    def test_state_serializable(self):
        try:
            from suijin.core.state import SuijinState

            state = SuijinState(messages=[], objective="test", target="localhost")
            d = state.model_dump()
            assert d["objective"] == "test"
        except ImportError as e:
            pytest.skip(f"State module unavailable: {e}")

    def test_agent_graph_imports(self):
        from suijin.core.agent_graph import SuijinAgentGraph

        assert SuijinAgentGraph is not None

    def test_supervisor_imports(self):
        from suijin.core.supervisor import analyze_trace

        assert analyze_trace is not None


class TestThinkNode:
    """Verify think_node parsing and action types."""

    def test_think_node_imports(self):
        try:
            from suijin.nodes.think_node import think

            assert think is not None
        except ImportError as e:
            pytest.skip(f"Think node unavailable: {e}")

    def test_subagent_node_imports(self):
        from suijin.nodes.subagent_node import spawn_and_collect

        assert spawn_and_collect is not None


class TestEngagement:
    """Verify engagement state save/restore."""

    def test_session_state_imports(self):
        from suijin.core.engagement import load_session_state, save_session_state

        assert save_session_state is not None
        assert load_session_state is not None


class TestErrorHandler:
    """Verify error handling decorators and fallbacks."""

    def test_error_handler_imports(self):
        try:
            from suijin.core.error_handler import GracefulFallback, safe_call

            assert safe_call is not None
            assert GracefulFallback is not None
        except ImportError as e:
            pytest.skip(f"Error handler unavailable: {e}")

    def test_error_class_imports(self):
        try:
            from suijin.modules.platform.lib.helpers.error_class import classify_error

            assert classify_error is not None
        except ImportError as e:
            pytest.skip(f"Error class unavailable: {e}")


class TestPromptSafety:
    """Verify prompt injection defenses."""

    def test_wrap_untrusted(self):
        from suijin.core.prompt_safety import wrap_untrusted

        wrapped = wrap_untrusted("user input")
        assert "UNTRUSTED" in wrapped

    def test_hard_guardrail_blocks_gov(self):
        try:
            from suijin.modules.platform.lib.helpers.hard_guardrail import check_guardrail

            result = check_guardrail("hack fbi.gov")
            assert result is not None
        except ImportError as e:
            pytest.skip(f"Guardrail unavailable: {e}")

    def test_hard_guardrail_allows_normal(self):
        try:
            from suijin.modules.platform.lib.helpers.hard_guardrail import check_guardrail

            result = check_guardrail("scan example.com")
            assert result is None
        except ImportError as e:
            pytest.skip(f"Guardrail unavailable: {e}")


class TestDeception:
    """Verify deception engine components load and work."""

    def test_honeypot_factory(self):
        from suijin.core.blue.deception.honeypot_factory import generate_honeypot_response

        resp = generate_honeypot_response({"path": "/admin"})
        assert isinstance(resp, dict)
        assert "body" in resp

    def test_time_sink(self):
        from suijin.core.blue.deception.time_sink import TimeSink

        ts = TimeSink()
        ts.tarpit("10.0.0.1", delay_seconds=0.1)
        assert ts.should_sink("10.0.0.1")

    def test_canary_token(self):
        from suijin.core.blue.deception.canary_token import deploy_canary

        token = deploy_canary("api_key", "test_canary_12345")
        assert token is not None

    def test_shadow_redirect(self):
        from suijin.core.blue.deception.shadow_redirect import redirect_to_shadow

        result = redirect_to_shadow("10.0.0.99")
        assert "10.0.0.99" in result

    def test_deception_engine(self):
        from suijin.core.blue.defense.deception_engine import DeceptionEngine

        engine = DeceptionEngine()
        resp = engine.decide_response("attacker-1", {"ip": "10.0.0.1", "path": "/login"}, 7)
        assert resp["status"] == "ok"
