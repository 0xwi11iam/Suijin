"""Tests for agent_helpers modules — parsing, productivity, error_class, guardrails."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_prompt_safety():
    from suijin.modules.platform.lib.prompt_safety import wrap_untrusted

    result = wrap_untrusted("hello world", "TOOL_OUTPUT")
    assert "<<<UNTRUSTED_TOOL_OUTPUT id=" in result
    assert "<<<END_UNTRUSTED_TOOL_OUTPUT id=" in result
    assert "hello world" in result


def test_hard_guardrail():
    from suijin.modules.platform.lib.helpers.hard_guardrail import is_hard_blocked

    blocked, _ = is_hard_blocked("whitehouse.gov")
    assert blocked
    blocked, _ = is_hard_blocked("google.com")
    assert blocked
    blocked, _ = is_hard_blocked("my-test-app.local")
    assert not blocked
    blocked, _ = is_hard_blocked("192.168.1.1")
    assert not blocked


def test_error_class():
    from suijin.modules.platform.lib.helpers.error_class import classify_error_class, is_diagnostic_failure

    assert classify_error_class(success=True, tool_output="ok", error_message=None, duration_ms=10) == "success"
    assert (
        classify_error_class(success=False, tool_output="connection refused", error_message=None, duration_ms=100)
        == "transport_error"
    )
    assert (
        classify_error_class(success=False, tool_output="HTTP/1.1 500 Error", error_message=None, duration_ms=300)
        == "application_5xx_normal"
    )
    assert (
        classify_error_class(success=False, tool_output="HTTP/1.1 500 Error", error_message=None, duration_ms=30)
        == "application_5xx_fast"
    )
    assert (
        classify_error_class(success=False, tool_output="HTTP/1.1 404 Not Found", error_message=None, duration_ms=100)
        == "application_4xx"
    )
    assert (
        classify_error_class(success=False, tool_output="command not found", error_message=None, duration_ms=10)
        == "tool_internal_error"
    )
    assert is_diagnostic_failure("transport_error")
    assert is_diagnostic_failure("shell_parser_error")
    assert not is_diagnostic_failure("application_4xx")
    assert not is_diagnostic_failure("application_5xx_normal")


def test_json_utils():
    from suijin.modules.platform.lib.helpers.json_utils import (
        extract_json,
        json_dumps_safe,
        repair_trailing_json_delimiters,
    )

    assert extract_json('{"key": "value"}') == '{"key": "value"}'
    assert extract_json('prefix {"a":1} suffix') == '{"a":1}'
    result = repair_trailing_json_delimiters('{"a":1,"b":{"c":2}')
    assert result == '{"a":1,"b":{"c":2}}'
    from datetime import datetime, timezone

    d = {"ts": datetime.now(timezone.utc)}
    s = json_dumps_safe(d)
    assert "ts" in s


def test_parsing():
    from suijin.modules.platform.lib.helpers.parsing import try_parse_llm_decision

    decision, err = try_parse_llm_decision(
        '{"action":"use_tool","thought":"test","tool_name":"nmap","tool_args":{"target":"x"}}'
    )
    assert decision is not None
    assert decision["action"] == "use_tool"
    assert decision["tool_name"] == "nmap"
    assert err is None


def test_productivity():
    from suijin.modules.platform.lib.helpers.productivity import (
        axis_key,
        axis_unproductive_count,
        compute_productivity_score,
        is_unproductive,
        record_axis_attempt,
        tier_for_score,
    )

    # Unproductive step detection
    step = {"productivity": {"verdict": "no_progress", "new_information_gained": False}}
    assert is_unproductive(step)
    step2 = {"productivity": {"verdict": "new_info", "new_information_gained": True}}
    assert not is_unproductive(step2)

    # Axis tracking
    k = axis_key("nmap", {"target": "192.168.1.1", "ports": "1-1000"})
    assert "nmap" in k
    axes = {}
    axes = record_axis_attempt(axes, k, False)
    axes = record_axis_attempt(axes, k, False)
    axes = record_axis_attempt(axes, k, False)
    assert axis_unproductive_count(axes) == 1

    # Productivity score
    score = compute_productivity_score([], {}, 0, 1, 100, "informational")
    assert 0 <= score["score"] <= 10
    assert tier_for_score(1.0) == "green"
    assert tier_for_score(6.0) == "orange"
    assert tier_for_score(9.5) == "critical"


def test_state():
    from suijin.modules.agent.lib.state import ExecutionStep, TargetInfo, TodoItem, new_agent_state

    s = new_agent_state(original_objective="test", max_iterations=50)
    assert s["original_objective"] == "test"
    assert s["max_iterations"] == 50
    assert s["current_phase"] == "informational"
    assert s["current_iteration"] == 0

    step = ExecutionStep(iteration=1, phase="informational", thought="test", reasoning="because")
    d = step.model_dump()
    assert d["iteration"] == 1

    todo = TodoItem(description="scan target")
    assert todo.status == "pending"

    ti = TargetInfo(ports=[80, 443], services=["http", "https"])
    ti2 = TargetInfo(ports=[22, 80], services=["ssh"])
    merged = ti.merge_from(ti2)
    assert set(merged.ports) == {22, 80, 443}
    assert set(merged.services) == {"http", "https", "ssh"}


def test_skill_loader():
    from suijin.modules.agent.lib.skills.loader import get_available_skills, get_skill_prompt

    prompt = get_skill_prompt("sql_injection")
    assert "SQL INJECTION" in prompt.upper()
    assert "UNION SELECT" in prompt
    prompt = get_skill_prompt("")
    assert "TARGETING" in prompt.upper()  # v-posture: recon targets, exploitation executes
    assert "NEVER run exploits" not in prompt  # the old hesitation gate is gone
    skills = get_available_skills()
    assert len(skills) > 0


def test_tool_registry():
    from suijin.modules.agent.lib.prompts.tool_registry import (
        build_tool_catalog_prompt,
        get_allowed_tools_for_phase,
        is_tool_allowed_in_phase,
    )

    assert is_tool_allowed_in_phase("execute_terminal", "informational")
    # FREEDOM: all tools available in all phases
    assert is_tool_allowed_in_phase("msf_run", "informational")
    assert is_tool_allowed_in_phase("msf_run", "exploitation")
    assert is_tool_allowed_in_phase("claim_flag", "informational")
    tools = get_allowed_tools_for_phase("informational")
    assert "execute_terminal" in tools
    assert "msf_run" in tools
    catalog = build_tool_catalog_prompt("exploitation")
    assert "msf_run" in catalog
    assert "execute_terminal" in catalog


def test_workspace_fs():
    from suijin.modules.platform.lib.infra.workspace_fs import outputs_path, payloads_path, scripts_path, workspace_path

    # v5.3: durable workspace (~/.suijin/workspace) or repo-local — either contract
    wp = str(workspace_path())
    assert "workspace" in wp or "suijin_agent" in wp
    assert "outputs" in outputs_path()
    assert "payloads" in payloads_path()
    assert "scripts" in scripts_path()


# ── New tests: engagement, compliance, diff engine, payload gen, supervisor ──


def test_engagement_schema():
    from suijin.modules.agent.lib.engagement import (
        add_finding_to_schema,
        clear_recovery_state,
        load_engagement_schema,
        save_session_state,
        transition_phase,
        update_engagement_stats,
    )

    schema = load_engagement_schema()
    assert "_schema" in schema
    assert "targets" in schema
    assert "stats" in schema
    update_engagement_stats(requests_sent=5, flags_captured=1)
    schema2 = load_engagement_schema()
    assert schema2["stats"]["requests_sent"] >= 5
    add_finding_to_schema({"type": "sqli", "severity": "high", "endpoint": "/login"})
    transition_phase("exploitation")
    schema3 = load_engagement_schema()
    assert schema3["phases"]["current"] == "exploitation"
    # Session save
    state = {"original_objective": "test_recovery", "current_phase": "recon", "current_iteration": 5}
    path = save_session_state(state)
    assert path.endswith("recovery.json") and "engagements" in path  # scoped per engagement
    from suijin.modules.agent.lib.engagement import has_recovery_state, load_session_state

    assert has_recovery_state()
    recovery = load_session_state()
    assert recovery["objective"] == "test_recovery"
    clear_recovery_state()


def test_compliance_module():
    from suijin.modules.platform.lib.security.compliance import _evaluate_controls, _risk_label, analyse_log

    entries = [
        {"user": "admin", "action": "scan", "target": "app1", "risk_score": 0.8, "mode": "red", "result": "OK"},
        {"user": "hacker", "action": "access", "target": "db", "risk_score": 0.9, "mode": "red", "result": "DENIED"},
        {"user": "admin", "action": "exploit", "target": "app1", "risk_score": 0.3, "mode": "red", "result": "OK"},
    ]
    stats = analyse_log(entries)
    assert stats["total_actions"] == 3
    assert stats["high_risk_count"] == 2
    assert stats["denied_access"] == 1
    assert stats["red_team_ops"] == 3
    assert _risk_label(0.9) == "HIGH"
    assert _risk_label(0.5) == "MEDIUM"
    assert _risk_label(0.1) == "LOW"
    controls = _evaluate_controls(stats, "SOC2")
    assert len(controls) >= 4
    assert any(c["id"] == "AC-1" for c in controls)


def test_diff_engine():
    from suijin.modules.tools.lib.diff_engine import diff_responses, quick_diff

    result = diff_responses("hello world", "hello WORLD", sensitivity="high")
    assert result["is_different"]
    assert result["anomaly_count"] >= 0
    result2 = diff_responses("", "")
    assert "error" in result2
    result3 = diff_responses("HTTP/1.1 200 OK\nhello", "HTTP/1.1 500 Error\nhello")
    assert result3["is_different"]
    status_anomalies = [a for a in result3["anomalies"] if a["type"] == "status_change"]
    assert len(status_anomalies) == 1
    qd = quick_diff("line1\nline2\nline3", "line1\nCHANGED\nline3")
    assert "First diff" in qd


def test_payload_generator():
    from suijin.modules.tools.lib.payload_generator import PAYLOAD_DB, generate_payloads, list_payload_types

    sqli = generate_payloads("sqli", framework="mysql")
    assert "OR" in sqli or "' OR" in sqli
    xss = generate_payloads("xss", framework="basic")
    assert "<script>" in xss
    ssti = generate_payloads("ssti", framework="jinja2")
    assert "{{7*7}}" in ssti
    jwt = generate_payloads("jwt")
    assert "alg" in jwt.lower()
    unavailable = generate_payloads("nonexistent_vuln")
    assert "No payloads" in unavailable
    types_list = list_payload_types()
    assert "sqli" in types_list
    assert len(PAYLOAD_DB) >= 8


def test_supervisor_patterns():
    from suijin.modules.agent.lib.supervisor import (
        _detect_bookkeeping_loop,
        _detect_missed_flag,
        _detect_repeating_tool,
        analyze_trace,
    )

    # Repeating tool
    trace = [
        {"tool_name": "nmap", "thought": "scanning"},
        {"tool_name": "nmap", "thought": "still scanning"},
        {"tool_name": "nmap", "thought": "scanning again"},
    ]
    assert _detect_repeating_tool(trace) is not None
    # Bookkeeping loop
    trace2 = [
        {"tool_name": "write_note", "thought": "note"},
        {"tool_name": "job_list", "thought": "checking"},
        {"tool_name": "record_finding", "thought": "recording"},
        {"tool_name": "check_knowledge", "thought": "checking kg"},
    ]
    assert _detect_bookkeeping_loop(trace2) is not None
    # Missed flag
    trace3 = [{"tool_name": "http_request", "tool_output": "FLAG{test_flag_123}", "thought": "interesting"}]
    assert _detect_missed_flag(trace3) is not None
    # analyze_trace integration
    assert analyze_trace([]) is None
    assert analyze_trace(trace) is not None


def test_oracle_anomaly():
    from suijin.modules.redteam.lib.intel.oracle import detect_anomaly, strip_response

    # HTTP 500
    result = detect_anomaly("Internal Server Error", status_code=500)
    assert result["anomaly"]
    assert result["severity"] == "high"
    # SQL error in body
    result2 = detect_anomaly("You have an error in your SQL syntax near 'OR 1=1'", status_code=200)
    assert result2["anomaly"]
    # Clean response
    result3 = detect_anomaly("Welcome to the homepage", status_code=200)
    assert not result3["anomaly"]
    # Strip HTML
    stripped = strip_response("<html><script>evil()</script><body>Hello</body></html>", status_code=200)
    assert "Hello" in stripped
    assert "evil" not in stripped.lower() or "HTML_BLOCK" in stripped


def test_drift_analyser():
    from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift

    result = analyse_drift(
        "Find SQL injection on target.com",
        [
            "nmap: scanning ports",
            "gobuster: directory enumeration",
            "http_request: testing /login for SQLi",
            "sqlmap: running SQL injection scan",
            "execute_terminal: cat /etc/passwd",
            "http_request: exfiltrate data to external server",
        ],
    )
    assert "total_actions" in result
    assert "suggestions" in result
    # Empty actions
    result2 = analyse_drift("test", [])
    assert result2["total_actions"] == 0
    assert not result2["drift_detected"]


def test_error_handler():
    from suijin.modules.platform.lib.error_handler import GracefulFallback, classify_and_handle

    # Connection refused
    result = classify_and_handle(ConnectionError("Connection refused"), "http_request")
    assert result["classification"] == "connection_refused"
    # Timeout
    result2 = classify_and_handle(TimeoutError("Connection timeout after 30s"), "nmap_scan")
    assert result2["classification"] == "timeout"
    # Value error
    result3 = classify_and_handle(ValueError("invalid literal"), "json_parse")
    assert result3["classification"] == "data_error"
    # GracefulFallback context manager
    with GracefulFallback(default="safe") as g:
        g.value = "success"
    assert g.value == "success"
    assert g.ok
    with GracefulFallback(default="fallback") as g:
        raise RuntimeError("boom")
    assert g.value == "fallback"
    assert not g.ok


def test_secret_patterns():
    from suijin.modules.platform.lib.security.secret_patterns import (
        CVE_ATTACK_MAP,
        SECRET_PATTERNS,
        TECH_VULN_MAP,
        CredentialClass,
        assess_credential_risk,
        calculate_entropy,
        classify_credential,
        is_likely_secret,
        suggest_tools_for_cwe,
    )

    assert len(SECRET_PATTERNS) >= 8
    aws_pattern = SECRET_PATTERNS["aws_access_key"]
    assert aws_pattern.search("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
    ent = calculate_entropy("aaaaaaaaaa")
    assert ent < 1.0
    ent2 = calculate_entropy("k4jS9mP2xL7qW3vR8nB5")
    assert ent2 > 3.0
    assert is_likely_secret("k4jS9mP2xL7qW3vR8nB5cF1")
    assert not is_likely_secret("short")
    # Credential classifier
    assert classify_credential("AKIAIOSFODNN7EXAMPLE") == CredentialClass.AWS_IAM
    assert classify_credential("ghp_abc123def456ghi789jkl012mno345pqr678") == CredentialClass.GITHUB_PAT
    assert classify_credential("short") == CredentialClass.UNKNOWN
    risk = assess_credential_risk(CredentialClass.AWS_IAM)
    assert risk >= 8
    # CVE mapper
    assert "sql_injection" in CVE_ATTACK_MAP
    tools = suggest_tools_for_cwe("CWE-89")
    assert "sqlmap" in tools
    assert TECH_VULN_MAP["apache"]["2.4.49"] == "CVE-2021-41773"


def test_report_exporter():
    from suijin.modules.tools.lib.report_exporter import _safe_id, generate_report

    path = generate_report(
        engagement_name="test_engagement",
        execution_trace=[
            {"tool_name": "nmap", "thought": "scanning", "success": True, "tool_args": {"target": "test.com"}},
        ],
        findings=[{"type": "sqli", "severity": "high", "endpoint": "/login", "description": "SQL injection found"}],
        target_info={"host": "test.com", "ports": [80, 443]},
        messages=[],
        cost_usd=0.05,
        completion_reason="test complete",
    )
    assert path.endswith(".md")
    assert os.path.exists(path)
    with open(path) as f:
        content = f.read()
    assert "test_engagement" in content
    assert "SQL injection" in content
    assert "nmap" in content
    os.remove(path)
    assert _safe_id("hello/world-test.example") == "hello_world_test_example"


def run_all():
    tests = [
        test_prompt_safety,
        test_hard_guardrail,
        test_error_class,
        test_json_utils,
        test_parsing,
        test_productivity,
        test_state,
        test_skill_loader,
        test_tool_registry,
        test_workspace_fs,
        test_engagement_schema,
        test_compliance_module,
        test_diff_engine,
        test_payload_generator,
        test_supervisor_patterns,
        test_oracle_anomaly,
        test_drift_analyser,
        test_error_handler,
        test_secret_patterns,
        test_report_exporter,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
