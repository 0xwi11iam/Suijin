"""Tests for dispatch.py — tool routing hub internals.

Covers pure functions, guardrail paths, file I/O, job tracking,
and route_tool dispatch with mocked execution.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


class TestPureHelpers:
    def test_truncate_short(self):
        from suijin.modules.tools.lib.dispatch import truncate

        assert truncate("hello") == "hello"

    def test_truncate_long(self):
        from suijin.modules.tools.lib.dispatch import truncate

        result = truncate("x" * 60000, limit=50000)
        assert result.startswith("x" * 50000)
        assert "TRUNCATED" in result

    def test_truncate_empty(self):
        from suijin.modules.tools.lib.dispatch import truncate

        assert truncate("") == ""

    def test_proxy_state_management(self):
        from suijin.modules.tools.lib.dispatch import get_proxy, reset_recon_state, set_proxy

        set_proxy("http://proxy.example:8080")
        assert get_proxy() == "http://proxy.example:8080"
        reset_recon_state()
        set_proxy(None)
        assert get_proxy() is None


class TestExecuteTerminalSafety:
    """Guardrails + self-kill protection without real subprocess."""

    def test_empty_command(self):
        from suijin.modules.tools.lib.dispatch import execute_terminal

        result = execute_terminal("")
        assert "No command" in result

    def test_self_kill_protection(self):
        """Command that kills the agent's own PID must be refused."""
        from suijin.modules.tools.lib.dispatch import execute_terminal

        my_pid = str(os.getpid())
        result = execute_terminal(f"kill -9 {my_pid}")
        assert "SYSTEM OVERRIDE" in result
        assert "Refusing" in result

    def test_dangerous_command_blocked(self):
        """rm -rf / must be blocked by guardrails."""
        from suijin.modules.tools.lib.dispatch import execute_terminal

        result = execute_terminal("rm -rf /")
        assert "denied" in result.lower()

    def test_safe_command_executes(self, monkeypatch):
        """Safe command runs via mocked subprocess."""
        import suijin.modules.tools.lib.dispatch as d
        import suijin.modules.tools.lib.result as result_mod

        calls = {}

        class FakeResult:
            returncode = 0
            stdout = "test output"
            stderr = ""

        def mock_run(cmd, **kwargs):
            calls["cmd"] = cmd
            return FakeResult()

        # execute_terminal shells out through tools/result.run_command
        monkeypatch.setattr(result_mod.subprocess, "run", mock_run)
        result = d.execute_terminal("echo hello")
        assert "test output" in result
        assert "cmd" in calls

    def test_command_timeout_handled(self, monkeypatch):
        """Timeout errors surface as error messages, not crashes."""
        import subprocess as sp

        import suijin.modules.tools.lib.dispatch as d
        import suijin.modules.tools.lib.result as result_mod

        def mock_run(cmd, **kwargs):
            raise sp.TimeoutExpired(cmd, timeout=1)

        monkeypatch.setattr(result_mod.subprocess, "run", mock_run)
        result = d.execute_terminal("sleep 100", timeout=1)
        assert "timed out" in result.lower()


class TestFileOps:
    def test_read_file_missing(self):
        from suijin.modules.tools.lib.dispatch import read_file

        # Use an allowlisted path (/tmp) that doesn't exist
        result = read_file("/tmp/suijin_definitely_missing_file_xyz.txt")
        assert isinstance(result, str)
        assert "not found" in result.lower()

    def test_read_file_outside_workspace_rejected(self):
        """Absolute paths outside workspace + allowlist are rejected — as an
        Error: string (tool contract: never raise)."""
        from suijin.modules.tools.lib.dispatch import read_file

        result = read_file("/etc/passwd")
        assert isinstance(result, str)
        assert result.startswith("Error:")
        assert "outside workspace" in result

    def test_write_and_read_roundtrip_tmp(self):
        """Write to /tmp (allowlisted) and read it back."""
        import uuid

        from suijin.modules.tools.lib.dispatch import read_file, write_file

        path = f"/tmp/suijin_test_{uuid.uuid4().hex[:8]}.txt"
        try:
            result = write_file(path, "test content 123")
            assert "written" in result.lower()
            content = read_file(path)
            assert "test content 123" in content
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_write_file_relative_workspace(self):
        """Relative paths resolve into the agent workspace."""
        import uuid

        from suijin.modules.tools.lib.dispatch import read_file, write_file

        rel = f"outputs/test_{uuid.uuid4().hex[:8]}.txt"
        try:
            result = write_file(rel, "workspace content")
            assert "written" in result.lower()
            content = read_file(rel)
            assert "workspace content" in content
        finally:
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

            p = WORKSPACE_DIR / rel
            if p.exists():
                p.unlink()


class TestCVSSHelpers:
    def test_is_kev_true(self):
        from suijin.modules.tools.lib.dispatch import _is_kev

        assert _is_kev({"cisaExploitAdd": "2024-01-01"}) is True
        assert _is_kev({"cisaActionDue": "2024-02-01"}) is True

    def test_is_kev_false(self):
        from suijin.modules.tools.lib.dispatch import _is_kev

        assert _is_kev({}) is False
        assert _is_kev({"vulnStatus": "Analyzed"}) is False

    def test_extract_cvss_v31(self):
        from suijin.modules.tools.lib.dispatch import _extract_cvss

        cve_data = {"metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]}}
        score, severity = _extract_cvss(cve_data)
        assert score == "9.8"
        assert severity == "CRITICAL"

    def test_extract_cvss_v30(self):
        from suijin.modules.tools.lib.dispatch import _extract_cvss

        cve_data = {"metrics": {"cvssMetricV30": [{"cvssData": {"baseScore": 7.5, "baseSeverity": "HIGH"}}]}}
        score, severity = _extract_cvss(cve_data)
        assert score == "7.5"
        assert severity == "HIGH"

    def test_extract_cvss_missing_returns_na(self):
        from suijin.modules.tools.lib.dispatch import _extract_cvss

        score, severity = _extract_cvss({})
        assert score == "N/A"
        assert severity == "UNKNOWN"


class TestJobTracking:
    def test_job_status_unknown(self):
        from suijin.modules.tools.lib.dispatch import _job_status

        result = _job_status("nonexistent-job-id")
        assert isinstance(result, str)

    def test_job_list(self):
        from suijin.modules.tools.lib.dispatch import _job_list

        result = _job_list()
        assert isinstance(result, str)

    def test_job_cancel_unknown(self):
        from suijin.modules.tools.lib.dispatch import _job_cancel

        result = _job_cancel("nonexistent-job-id")
        assert isinstance(result, str)


class TestRouteTool:
    def test_unknown_tool(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        result = route_tool("nonexistent_tool_xyz", {}, {})
        assert "TOOL NOT FOUND" in result and "ASK THE OPERATOR" in result

    def test_route_http_request_mocked(self, monkeypatch):
        """route_tool('http_request') uses the mocked module function.

        Module tools (from Tools/) shadow builtin routes, so we neutralize
        get_module_tools first to test the builtin route.
        """
        import suijin.modules.tools.lib.dispatch as d

        def mock_http(method, url, headers=None, body=""):
            return "Mocked response"

        monkeypatch.setattr(d, "get_module_tools", lambda: {})
        monkeypatch.setattr(d, "http_request", mock_http)
        result = d.route_tool("http_request", {"url": "http://test.local", "method": "GET"}, {})
        assert "Mocked" in result

    def test_route_execute_terminal_self_kill(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        my_pid = str(os.getpid())
        result = route_tool("execute_terminal", {"cmd": f"kill {my_pid}"}, {})
        assert "SYSTEM OVERRIDE" in result

    def test_route_deploy_subagent_guidance(self):
        """deploy_subagent used as tool_name must return self-correction guidance."""
        from suijin.modules.tools.lib.dispatch import route_tool

        result = route_tool("deploy_subagent", {}, {})
        assert "WRONG FORMAT" in result

    def test_route_claim_flag(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        result = route_tool("claim_flag", {"flag": "FLAG{test}"}, {})
        assert "FLAG{test}" in result

    def test_get_tool_catalog_is_markdown(self):
        from suijin.modules.tools.lib.dispatch import get_tool_catalog

        catalog = get_tool_catalog()
        assert isinstance(catalog, str)
        assert len(catalog) > 0


class TestCatalogParity:
    def test_every_routed_tool_is_advertised(self):
        """Flexibility contract: a tool the model cannot see is a tool that
        does not exist. v5.1: FULL parity — the kernel-rendered reference
        includes every registered tool (deploy_subagent included; calling
        it returns corrective ACTION guidance by design). Zero invisible."""
        from suijin.modules.loader import discover_modules

        discover_modules()

        from suijin.modules.tools.lib import dispatch

        catalog = dispatch.get_tool_catalog()
        routes = set(dispatch._build_routes(None).keys())
        missing = sorted(n for n in routes if n not in catalog)
        assert missing == [], f"routed but invisible to the model: {missing}"


class TestToolNotFound:
    def test_close_match_suggested(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("nmap_scanx", {}, {})
        assert "TOOL NOT FOUND" in out and "nmap_scan" in out and "ask_operator".upper() in out.upper()

    def test_no_match_points_at_operator(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("zzz_totally_unknown", {}, {})
        assert "TOOL NOT FOUND" in out and "ASK THE OPERATOR" in out and "Do NOT guess" in out

    def test_ask_operator_rule_in_prompt(self):
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.tools.lib import dispatch

        assert "ONE guess maximum" in dispatch.get_tool_catalog()
