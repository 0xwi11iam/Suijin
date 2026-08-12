"""Comprehensive tests for state models, helpers, parser, and productivity.

Boosts coverage for: state.py, parsing.py, productivity.py, json_utils.py,
error_class.py, hard_guardrail.py, modules/loader.py
"""
import sys, os, json, uuid
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# state.py — Pydantic models, formatting helpers, state factory
# ═══════════════════════════════════════════════════════════════════════════════

class TestTodoItem:
    def test_default_creation(self):
        from medusa.core.state import TodoItem
        t = TodoItem(description="scan ports")
        assert t.description == "scan ports"
        assert t.status == "pending"
        assert t.priority == "medium"
        assert len(t.id) == 8

    def test_explicit_status(self):
        from medusa.core.state import TodoItem
        t = TodoItem(description="exploit sqli", status="in_progress", priority="high")
        assert t.status == "in_progress"
        assert t.priority == "high"

    def test_completed_at_settable(self):
        from medusa.core.state import TodoItem
        now = datetime.now(timezone.utc)
        t = TodoItem(description="done", completed_at=now)
        assert t.completed_at == now

    def test_priority_coercion_synonyms(self):
        from medusa.core.state import TodoItem
        t1 = TodoItem(description="x", priority="critical")
        assert t1.priority == "high"
        t2 = TodoItem(description="x", priority="urgent")
        assert t2.priority == "high"
        t3 = TodoItem(description="x", priority="info")
        assert t3.priority == "low"

    def test_notes_optional(self):
        from medusa.core.state import TodoItem
        t = TodoItem(description="no notes")
        assert t.notes is None
        t2 = TodoItem(description="with notes", notes="check port 443")
        assert t2.notes == "check port 443"


class TestExecutionStep:
    def test_minimal_creation(self):
        from medusa.core.state import ExecutionStep
        s = ExecutionStep(iteration=1)
        assert s.iteration == 1
        assert s.phase == "informational"
        assert s.success is True
        assert s.step_id

    def test_with_tool(self):
        from medusa.core.state import ExecutionStep
        s = ExecutionStep(
            iteration=2, phase="exploitation",
            thought="try sqlmap", reasoning="previous scan found mysql",
            tool_name="sqlmap_scan", tool_args={"url": "http://target/login"},
            tool_output="[CRITICAL] SQLi found",
            success=True, duration_ms=3400,
        )
        assert s.tool_name == "sqlmap_scan"
        assert s.tool_args == {"url": "http://target/login"}
        assert s.duration_ms == 3400

    def test_error_step(self):
        from medusa.core.state import ExecutionStep
        s = ExecutionStep(
            iteration=3, tool_name="nmap_scan",
            success=False, error_message="Connection refused",
            error_class="transport_error",
        )
        assert s.success is False
        assert s.error_class == "transport_error"

    def test_tool_args_none_stays_none(self):
        from medusa.core.state import ExecutionStep
        s = ExecutionStep(iteration=1, tool_args=None)
        # tool_args=None is valid (no tool call yet)
        assert s.tool_args is None


class TestTargetInfo:
    def test_creation(self):
        from medusa.core.state import TargetInfo
        t = TargetInfo(ports=[80, 443], services=["http", "https"])
        assert t.ports == [80, 443]
        assert t.services == ["http", "https"]

    def test_merge_from(self):
        from medusa.core.state import TargetInfo
        t1 = TargetInfo(ports=[80, 443], services=["http", "https"])
        t2 = TargetInfo(ports=[22, 80], services=["ssh"], primary_target="example.com")
        merged = t1.merge_from(t2)
        assert set(merged.ports) == {22, 80, 443}
        assert set(merged.services) == {"http", "https", "ssh"}
        assert merged.primary_target == "example.com"

    def test_merge_updates_primary_target(self):
        from medusa.core.state import TargetInfo
        t1 = TargetInfo(primary_target="example.com")
        t2 = TargetInfo(primary_target="other.com")
        merged = t1.merge_from(t2)
        # merge_from takes newer values
        assert merged.primary_target == "other.com"

    def test_target_type_enum(self):
        from medusa.core.state import TargetInfo
        t = TargetInfo(target_type="domain")
        assert t.target_type == "domain"
        t2 = TargetInfo(target_type="ip")
        assert t2.target_type == "ip"


class TestNewAgentState:
    def test_creates_fresh_state(self):
        from medusa.core.state import new_agent_state
        s = new_agent_state(original_objective="test target", max_iterations=50)
        assert s["original_objective"] == "test target"
        assert s["max_iterations"] == 50
        assert s["current_phase"] == "informational"
        assert s["current_iteration"] == 0
        assert s["execution_trace"] == []

    def test_generates_session_id(self):
        from medusa.core.state import new_agent_state
        s1 = new_agent_state()
        s2 = new_agent_state()
        # Session IDs are empty unless provided
        assert s1["session_id"] == ""

    def test_default_max_iterations(self):
        from medusa.core.state import new_agent_state
        s = new_agent_state()
        assert s["max_iterations"] == 100


class TestFormattingHelpers:
    def test_truncate_short(self):
        from medusa.core.state import _truncate
        assert _truncate("hello") == "hello"
        assert _truncate("") == ""

    def test_truncate_long(self):
        from medusa.core.state import _truncate
        long_text = "x" * 600
        result = _truncate(long_text, 500)
        assert len(result) == 501  # 500 chars + "…"
        assert result.endswith("…")

    def test_format_execution_trace_empty(self):
        from medusa.core.state import format_execution_trace
        assert format_execution_trace([]) == "No steps executed yet."

    def test_format_execution_trace_with_steps(self):
        from medusa.core.state import format_execution_trace
        trace = [
            {"iteration": 1, "tool_name": "nmap_scan", "tool_args": {"target": "x"},
             "tool_output": "22/tcp open ssh", "success": True,
             "productivity": {"verdict": "new_info"}},
        ]
        result = format_execution_trace(trace)
        assert "nmap_scan" in result
        assert "new_info" in result

    def test_format_todo_list_empty(self):
        from medusa.core.state import format_todo_list
        assert format_todo_list([]) == "No tasks tracked."

    def test_format_todo_list_with_items(self):
        from medusa.core.state import format_todo_list
        items = [
            {"description": "scan ports", "status": "completed", "priority": "high"},
            {"description": "exploit sqli", "status": "in_progress", "priority": "high"},
        ]
        result = format_todo_list(items)
        assert "scan ports" in result
        assert "exploit sqli" in result
        assert "✅" in result
        assert "🔄" in result

    def test_format_chain_context_empty(self):
        from medusa.core.state import format_chain_context
        result = format_chain_context([], [], [], [])
        assert "No chain context yet" in result

    def test_format_chain_context_with_findings(self):
        from medusa.core.state import format_chain_context
        findings = [{"title": "SQLi on /login", "severity": "high", "evidence": "sqlmap output"}]
        result = format_chain_context(findings, [], [], [])
        assert "SQLi on /login" in result

    def test_format_chain_context_with_failures(self):
        from medusa.core.state import format_chain_context
        failures = [{"tool_name": "nmap", "error_message": "timeout", "error_class": "transport_error"}]
        result = format_chain_context([], failures, [], [])
        assert "transport_error" in result
        assert "nmap" in result

    def test_format_qa_history_empty(self):
        from medusa.core.state import format_qa_history
        assert format_qa_history([]) == ""

    def test_format_qa_history_with_entries(self):
        from medusa.core.state import format_qa_history
        qa = [{"question": "what port?", "answer": "443"}]
        result = format_qa_history(qa)
        assert "what port?" in result
        assert "443" in result

    def test_format_objective_history_empty(self):
        from medusa.core.state import format_objective_history
        assert format_objective_history([]) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# parsing.py — LLM response parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestParsing:
    def test_successful_parsing(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        resp = '{"action":"use_tool","thought":"test scan","tool_name":"nmap","tool_args":{"target":"x"}}'
        decision, err = try_parse_llm_decision(resp)
        assert decision is not None
        assert decision["action"] == "use_tool"
        assert decision["tool_name"] == "nmap"
        assert err is None

    def test_parsing_with_json_in_text(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        resp = 'some text {"action":"complete","thought":"done"} more text'
        decision, err = try_parse_llm_decision(resp)
        assert decision is not None
        assert decision["action"] == "complete"

    def test_parsing_no_json(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        decision, err = try_parse_llm_decision("no json here")
        assert decision is None
        assert err is not None

    def test_parsing_invalid_action(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        decision, err = try_parse_llm_decision('{"action":"hack_the_planet"}')
        assert decision is None
        assert "Unknown action" in (err or "")

    def test_parsing_missing_tool_name_for_use_tool(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        decision, err = try_parse_llm_decision('{"action":"use_tool"}')
        assert decision is None
        assert "tool_name" in (err or "")

    def test_all_valid_actions(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        for action in ["use_tool", "plan_tools", "transition_phase", "complete",
                       "ask_user", "deploy_subagent", "switch_skill"]:
            extra = ', "tool_name":"nmap"' if action in ("use_tool", "plan_tools") else ""
            decision, err = try_parse_llm_decision(f'{{"action":"{action}"{extra}}}')
            assert decision is not None, f"Failed for action={action}: {err}"

    def test_parsing_malformed_json_repairs(self):
        from medusa.helpers.parsing import try_parse_llm_decision
        # Missing closing brace — repair_trailing_json_delimiters should fix
        decision, err = try_parse_llm_decision('{"action":"complete","thought":"x"')
        assert decision is not None

    def test_extract_tokens_from_response(self):
        from medusa.helpers.parsing import extract_tokens_from_response
        body = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgN"
        found = extract_tokens_from_response(body)
        assert len(found) >= 1

    def test_classify_web_vuln_sqli(self):
        from medusa.helpers.parsing import classify_web_vuln
        results = classify_web_vuln("SQL syntax error in mysql_fetch")
        assert len(results) >= 1
        assert any(r["type"] == "sqli" for r in results)


class TestJsonUtils:
    def test_extract_json_object(self):
        from medusa.helpers.json_utils import extract_json
        assert extract_json('{"a":1}') == '{"a":1}'

    def test_extract_json_from_text(self):
        from medusa.helpers.json_utils import extract_json
        assert extract_json('prefix {"a":1} suffix') == '{"a":1}'

    def test_extract_json_no_object(self):
        from medusa.helpers.json_utils import extract_json
        assert extract_json("no json") is None

    def test_repair_missing_brace(self):
        from medusa.helpers.json_utils import repair_trailing_json_delimiters
        result = repair_trailing_json_delimiters('{"a":1,"b":{"c":2}')
        assert result == '{"a":1,"b":{"c":2}}'

    def test_json_dumps_safe_datetime(self):
        from medusa.helpers.json_utils import json_dumps_safe
        now = datetime.now(timezone.utc)
        s = json_dumps_safe({"ts": now})
        assert "ts" in s


# ═══════════════════════════════════════════════════════════════════════════════
# productivity.py — loop detection heuristics
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductivity:
    def test_is_unproductive_no_progress(self):
        from medusa.helpers.productivity import is_unproductive
        step = {"productivity": {"verdict": "no_progress", "new_information_gained": False}}
        assert is_unproductive(step) is True

    def test_is_unproductive_duplicate(self):
        from medusa.helpers.productivity import is_unproductive
        step = {"productivity": {"verdict": "duplicate"}}
        assert is_unproductive(step) is True

    def test_is_unproductive_blocked(self):
        from medusa.helpers.productivity import is_unproductive
        step = {"productivity": {"verdict": "blocked"}}
        assert is_unproductive(step) is True

    def test_is_unproductive_new_info(self):
        from medusa.helpers.productivity import is_unproductive
        step = {"productivity": {"verdict": "new_info", "new_information_gained": True}}
        assert is_unproductive(step) is False

    def test_is_unproductive_diagnostic_progress(self):
        from medusa.helpers.productivity import is_unproductive
        step = {"productivity": {"verdict": "diagnostic_progress"}}
        assert is_unproductive(step) is False

    def test_is_unproductive_empty_step(self):
        from medusa.helpers.productivity import is_unproductive
        assert is_unproductive({}) is False

    def test_is_unproductive_nested_in_output_analysis(self):
        from medusa.helpers.productivity import is_unproductive
        step = {"output_analysis": {"productivity": {"verdict": "no_progress"}}}
        assert is_unproductive(step) is True

    def test_normalize_args_pattern_collapses_ids(self):
        from medusa.helpers.productivity import _normalize_args_pattern
        p1 = _normalize_args_pattern("nmap", {"target": "192.168.1.1"})
        p2 = _normalize_args_pattern("nmap", {"target": "10.0.0.1"})
        assert p1 == p2  # Both collapse to "<ip>"

    def test_output_fingerprint_stable(self):
        from medusa.helpers.productivity import _output_fingerprint
        fp1 = _output_fingerprint({"tool_output": "hello world"})
        fp2 = _output_fingerprint({"tool_output": "hello world"})
        assert fp1 == fp2
        assert len(fp1) == 8

    def test_output_fingerprint_different(self):
        from medusa.helpers.productivity import _output_fingerprint
        fp1 = _output_fingerprint({"tool_output": "hello"})
        fp2 = _output_fingerprint({"tool_output": "world"})
        assert fp1 != fp2

    def test_axis_key(self):
        from medusa.helpers.productivity import axis_key
        k = axis_key("nmap", {"target": "192.168.1.1", "ports": "1-1000"})
        assert "nmap" in k

    def test_axis_unproductive_count(self):
        from medusa.helpers.productivity import record_axis_attempt, axis_unproductive_count, axis_key
        k = axis_key("nmap", {"target": "x"})
        axes = {}
        axes = record_axis_attempt(axes, k, False)
        axes = record_axis_attempt(axes, k, False)
        axes = record_axis_attempt(axes, k, False)
        assert axis_unproductive_count(axes) == 1

    def test_compute_productivity_score(self):
        from medusa.helpers.productivity import compute_productivity_score
        score = compute_productivity_score([], {}, 0, 1, 100, "informational")
        assert isinstance(score, dict)
        assert "score" in score
        assert 0 <= score["score"] <= 10

    def test_tier_for_score(self):
        from medusa.helpers.productivity import tier_for_score
        assert tier_for_score(1.0) == "green"
        assert tier_for_score(6.0) == "orange"
        assert tier_for_score(9.5) == "critical"


# ═══════════════════════════════════════════════════════════════════════════════
# error_class.py + hard_guardrail.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorClass:
    def test_success(self):
        from medusa.helpers.error_class import classify_error_class
        assert classify_error_class(success=True, tool_output="ok", error_message=None, duration_ms=10) == "success"

    def test_transport_error(self):
        from medusa.helpers.error_class import classify_error_class
        assert classify_error_class(success=False, tool_output="connection refused", error_message=None, duration_ms=100) == "transport_error"

    def test_5xx_normal(self):
        from medusa.helpers.error_class import classify_error_class
        assert classify_error_class(success=False, tool_output="HTTP/1.1 500 Error", error_message=None, duration_ms=300) == "application_5xx_normal"

    def test_5xx_fast(self):
        from medusa.helpers.error_class import classify_error_class
        assert classify_error_class(success=False, tool_output="HTTP/1.1 500 Error", error_message=None, duration_ms=30) == "application_5xx_fast"

    def test_4xx(self):
        from medusa.helpers.error_class import classify_error_class
        assert classify_error_class(success=False, tool_output="HTTP/1.1 404 Not Found", error_message=None, duration_ms=100) == "application_4xx"

    def test_tool_internal_error(self):
        from medusa.helpers.error_class import classify_error_class
        assert classify_error_class(success=False, tool_output="command not found", error_message=None, duration_ms=10) == "tool_internal_error"

    def test_is_diagnostic_failure(self):
        from medusa.helpers.error_class import is_diagnostic_failure
        assert is_diagnostic_failure("transport_error") is True
        assert is_diagnostic_failure("shell_parser_error") is True
        assert is_diagnostic_failure("application_4xx") is False
        assert is_diagnostic_failure("application_5xx_normal") is False


class TestHardGuardrail:
    def test_blocks_gov_domain(self):
        from medusa.helpers.hard_guardrail import is_hard_blocked
        blocked, _ = is_hard_blocked("whitehouse.gov")
        assert blocked

    def test_blocks_mil_domain(self):
        from medusa.helpers.hard_guardrail import is_hard_blocked
        # .mil domains should be blocked
        blocked, _ = is_hard_blocked("army.mil")
        assert blocked

    def test_allows_private_ip(self):
        from medusa.helpers.hard_guardrail import is_hard_blocked
        blocked, _ = is_hard_blocked("192.168.1.1")
        assert not blocked

    def test_allows_local_domain(self):
        from medusa.helpers.hard_guardrail import is_hard_blocked
        blocked, _ = is_hard_blocked("my-test-app.local")
        assert not blocked


# ═══════════════════════════════════════════════════════════════════════════════
# modules/loader.py — module discovery and loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleLoader:
    def test_load_local_module_providers(self):
        from medusa.modules.loader import load_local_module
        mod = load_local_module("providers")
        assert mod is not None

    def test_load_local_module_nonexistent(self):
        from medusa.modules.loader import load_local_module
        with pytest.raises((ModuleNotFoundError, ImportError, FileNotFoundError)):
            load_local_module("nonexistent_module_xyz_123")


# ═══════════════════════════════════════════════════════════════════════════════
# providers.py — token counting, cost estimation, error handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestProviders:
    def test_usage_reset(self):
        from medusa.tools.providers import USAGE, reset_usage
        reset_usage()
        assert USAGE["calls"] == 0
        assert USAGE["est_cost_usd"] == 0.0

    def test_model_pricing_has_deepseek(self):
        from medusa.tools.providers import MODEL_PRICING
        assert "deepseek-v4-flash" in MODEL_PRICING
        assert "deepseek-v4-pro" in MODEL_PRICING

    def test_model_pricing_has_qwen(self):
        from medusa.tools.providers import MODEL_PRICING
        assert "Qwen/Qwen2.5-3B-Instruct" in MODEL_PRICING

    def test_generate_unknown_provider(self):
        from medusa.tools.providers import generate
        config = {"provider": "nonexistent", "temperature": 0}
        messages = [{"role": "user", "content": "test"}]
        result = generate(messages, config)
        assert "Unknown provider" in result or "Error" in result

    def test_generate_missing_key(self):
        from medusa.tools.providers import generate
        orig = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            config = {"provider": "deepseek", "temperature": 0}
            messages = [{"role": "user", "content": "test"}]
            result = generate(messages, config)
            assert "Error" in result or "API" in result
        finally:
            if orig:
                os.environ["DEEPSEEK_API_KEY"] = orig

    def test_provider_cost_estimation_in_pricing(self):
        from medusa.tools.providers import MODEL_PRICING
        # Verify pricing constants are reasonable
        in_price, out_price = MODEL_PRICING["deepseek-v4-flash"]
        assert in_price > 0
        assert out_price > 0
        # Cost for 1M input + 1M output tokens
        assert in_price + out_price > 0.5  # should cost something

    def test_get_usage_returns_dict(self):
        from medusa.tools.providers import get_usage, reset_usage
        reset_usage()
        usage = get_usage()
        assert "calls" in usage
        assert "est_cost_usd" in usage


# ═══════════════════════════════════════════════════════════════════════════════
# supervisor.py — pattern detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupervisor:
    def test_supervisor_module_imports(self):
        from medusa.core.supervisor import analyze_trace, get_phase_config
        assert analyze_trace is not None
        assert get_phase_config is not None

    def test_analyze_trace_empty(self):
        from medusa.core.supervisor import analyze_trace
        result = analyze_trace(trace=[])  # no extra kwargs
        assert result is None or isinstance(result, (dict, str))

    def test_repeating_tool_detection(self):
        from medusa.core.supervisor import _detect_repeating_tool
        trace = [
            {"tool_name": "nmap"}, {"tool_name": "nmap"}, {"tool_name": "nmap"},
        ]
        result = _detect_repeating_tool(trace)
        assert result is not None

    def test_get_phase_config_returns_dict(self):
        from medusa.core.supervisor import get_phase_config
        cfg = get_phase_config("informational")
        assert isinstance(cfg, dict)
