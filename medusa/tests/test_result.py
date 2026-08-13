"""Tests for medusa/tools/result.py — structured command results."""
from medusa.tools.result import CommandResult, run_command


class TestCommandResult:
    def test_format_includes_structured_fields(self):
        r = CommandResult("nmap -sV 127.0.0.1", 0, "Starting Nmap", "", 2100)
        text = r.format()
        assert "[COMMAND] nmap -sV 127.0.0.1" in text
        assert "[EXIT] 0 (2.1s)" in text
        assert "[STDOUT]" in text
        assert "Starting Nmap" in text

    def test_to_dict_shape(self):
        r = CommandResult("echo hi", 0, "hi", "", 5)
        d = r.to_dict()
        assert set(d) == {"command", "exit_code", "stdout", "stderr", "duration_ms"}
        assert d["exit_code"] == 0
        assert d["stdout"] == "hi"


class TestRunCommand:
    def test_run_command_executes(self):
        r = run_command(["echo", "structured_ok"])
        assert r.exit_code == 0
        assert "structured_ok" in r.stdout

    def test_run_command_timeout(self):
        r = run_command(["sleep", "30"], timeout=1)
        assert r.exit_code == -1
        assert "timed out" in r.stderr

    def test_run_command_missing_binary(self):
        r = run_command(["definitely_not_a_real_binary_xyz"])
        assert r.exit_code == -1
        assert "command not found" in r.stderr

    def test_execute_terminal_structured(self):
        from medusa.tools.terminal import execute_terminal

        result = execute_terminal("echo structured_term_ok")
        assert "[COMMAND] echo structured_term_ok" in result
        assert "[EXIT]" in result
        assert "structured_term_ok" in result
