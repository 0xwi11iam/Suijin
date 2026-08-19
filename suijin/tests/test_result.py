"""Tests for suijin/tools/result.py — structured command results."""

from suijin.modules.tools.lib.result import CommandResult, run_command


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
        from suijin.modules.tools.lib.terminal import execute_terminal

        result = execute_terminal("echo structured_term_ok")
        assert "[COMMAND] echo structured_term_ok" in result
        assert "[EXIT]" in result
        assert "structured_term_ok" in result


class TestStreaming:
    def test_run_command_streams_lines_to_sink(self):
        from suijin.modules.tools.lib.result import clear_stream_sink, set_stream_sink

        received = []
        set_stream_sink(received.append)
        try:
            r = run_command("for i in 1 2 3; do echo line$i; sleep 0.05; done", shell=True, timeout=10)
        finally:
            clear_stream_sink()
        assert r.exit_code == 0
        assert any("line1" in x for x in received)
        assert "line3" in r.stdout

    def test_run_command_streaming_timeout(self):
        from suijin.modules.tools.lib.result import clear_stream_sink, set_stream_sink

        received = []
        set_stream_sink(received.append)
        try:
            r = run_command("sleep 30", shell=True, timeout=1)
        finally:
            clear_stream_sink()
        assert r.exit_code == -1
        assert "timed out" in r.stderr


class TestBackgroundJobStreaming:
    def test_spawn_background_job_streams_output(self):
        import time

        from suijin.modules.tools.lib.result import run_command
        from suijin.nodes.execute_tool_node import _job_lock, _jobs, _spawn_background_job

        def fake_route(tool, args, config):
            return run_command("for i in 1 2 3; do echo jobline$i; sleep 0.05; done", shell=True).format()

        jid = _spawn_background_job("execute_terminal", {"cmd": "x"}, fake_route)
        for _ in range(100):
            with _job_lock:
                job = _jobs.get(jid)
            if job and job["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert job is not None
        assert job["status"] == "done"
        assert "jobline3" in job["output"]
