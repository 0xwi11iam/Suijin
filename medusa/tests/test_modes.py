"""Tests for operational-mode enforcement (mode_hitl / mode_guardrail).

These modes are described in the system prompt (prompts/base.py) and enforced
at the dispatch chokepoint (tools/modes.py) so they cannot be bypassed by a
misbehaving model.
"""

from medusa.tools.modes import _segment_binaries, check_mode_restrictions


class TestSegmentBinaries:
    """Compound-command parsing — each sub-command's binary must be checked."""

    def test_single_command(self):
        assert _segment_binaries("nmap -sV target") == ["nmap"]

    def test_semicolon_evasion(self):
        assert _segment_binaries("nmap -sV target; rm -rf /tmp/x") == ["nmap", "rm"]

    def test_pipe_and_chains(self):
        assert _segment_binaries("cat foo | grep bar && echo done || echo fail") == ["cat", "grep", "echo", "echo"]


class TestModesOff:
    def test_no_modes_allows_everything(self):
        assert (
            check_mode_restrictions("msf_run", {"module": "exploit/x"}, {"mode_hitl": False, "mode_guardrail": False})
            is None
        )

    def test_none_config(self):
        assert check_mode_restrictions("execute_terminal", {"cmd": "rm -rf /"}, None) is None


class TestHitlMode:
    cfg = {"mode_hitl": True}

    def test_flag_string_coercion(self):
        """config.json on disk stores booleans as strings — must still engage."""
        assert check_mode_restrictions("msf_run", {}, {"mode_hitl": "True"}) is not None
        assert check_mode_restrictions("msf_run", {}, {"mode_hitl": "False"}) is None

    def test_blocks_exploit_tools(self):
        for tool in ("msf_run", "msf_command", "msf_sessions", "apply_patch", "write_file", "pip_install"):
            msg = check_mode_restrictions(tool, {}, self.cfg)
            assert msg is not None and "HITL" in msg, tool

    def test_blocks_unknown_and_module_tools(self):
        assert check_mode_restrictions("some_module_tool", {}, self.cfg) is not None

    def test_allows_recon_tools(self):
        for tool in ("search_cve", "write_note", "recon_chain", "check_knowledge", "web_search", "http_request"):
            assert check_mode_restrictions(tool, {}, self.cfg) is None, tool

    def test_allows_recon_binaries_in_terminal(self):
        assert check_mode_restrictions("execute_terminal", {"cmd": "nmap -sV -sC target"}, self.cfg) is None

    def test_blocks_exploit_binaries_in_terminal(self):
        msg = check_mode_restrictions("execute_terminal", {"cmd": "sqlmap -u http://t --batch"}, self.cfg)
        assert msg is not None and "HITL" in msg

    def test_blocks_trailing_payload_evasion(self):
        msg = check_mode_restrictions("execute_terminal", {"cmd": "nmap target; python3 -c 'exploit()'"}, self.cfg)
        assert msg is not None

    def test_blocks_empty_command(self):
        assert check_mode_restrictions("execute_terminal", {"cmd": " "}, self.cfg) is not None


class TestGuardrailMode:
    cfg = {"mode_guardrail": True}

    def test_blocks_destructive_binaries(self):
        for cmd in ("rm -rf /tmp/x", "mv a b", "chmod 777 f", "kill -9 123", "pkill python"):
            msg = check_mode_restrictions("execute_terminal", {"cmd": cmd}, self.cfg)
            assert msg is not None and "guardrail" in msg, cmd

    def test_allows_readonly_binaries(self):
        assert check_mode_restrictions("execute_terminal", {"cmd": "ls -la && cat foo"}, self.cfg) is None

    def test_blocks_hidden_segment(self):
        msg = check_mode_restrictions("execute_terminal", {"cmd": "curl http://t || rm x"}, self.cfg)
        assert msg is not None

    def test_blocks_msf_command_destruction(self):
        msg = check_mode_restrictions("msf_command", {"cmd": "kill sessions"}, self.cfg)
        assert msg is not None

    def test_non_terminal_tools_unaffected(self):
        assert check_mode_restrictions("write_note", {"content": "note"}, self.cfg) is None


class TestRouteToolEnforcement:
    """The modes must be enforced by the public dispatcher, not just the helper."""

    def test_route_tool_blocks_in_hitl(self):
        from medusa.tools.dispatch import route_tool

        result = route_tool("msf_run", {"module": "exploit/unix/ftp"}, {"mode_hitl": True})
        assert isinstance(result, str) and "HITL" in result

    def test_route_tool_allows_when_off(self):
        from medusa.tools.dispatch import route_tool

        # route to a tool that does no harm with empty args
        result = route_tool("search_kb", {"keyword": ""}, {"mode_hitl": False})
        assert "HITL" not in str(result)
