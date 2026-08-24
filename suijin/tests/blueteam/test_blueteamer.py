"""Tests for blueteamer.py — Blue Team entry point and TUI.

Covers _find_free_port, _print_middleware_snippet, _init_firewall,
and _run_async choice branches (with heavy mocking of LLM/subprocess
dependencies). Was 0% covered.
"""

import asyncio
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import suijin.modules.blueteam.lib.blueteamer as bt


def _scripted_input(answers):
    """Return a console.input replacement that pops scripted answers."""
    answers = list(answers)

    def fake_input(prompt=""):
        if answers:
            return answers.pop(0)
        raise EOFError("no more scripted input")

    return fake_input


@pytest.fixture
def blue_mocks(monkeypatch, tmp_path):
    """Mock all heavyweight dependencies of _run_async."""
    mocks = {}

    # Session — SimpleNamespace instead of BlueSession
    fake_session = types.SimpleNamespace(
        endpoints_discovered=0,
        subagents_deployed=0,
        active_watchers=0,
        total_requests_processed=0,
        baseline_established=False,
        baseline_request_count=0,
        threats_blocked=0,
        threats_deceived=0,
    )
    fake_session.save = lambda: None
    monkeypatch.setattr(bt, "init_session", lambda target: fake_session)
    mocks["session"] = fake_session

    # Codebase scanner -> 1 fake endpoint
    monkeypatch.setattr(
        "suijin.modules.blueteam.lib.blue.codebase.scanner.scan_codebase",
        lambda root: [{"method": "GET", "path": "/health", "framework": "flask", "auth": "none"}],
    )
    mocks["endpoints"] = [{"method": "GET", "path": "/health", "framework": "flask", "auth": "none"}]

    # Watchers -> async no-op
    async def fake_spawn(endpoints, config):
        return []

    monkeypatch.setattr("suijin.modules.blueteam.lib.blue.watchers.spawn_watchers", fake_spawn)

    # SOC team -> cheap fakes
    class FakeSOCLead:
        campaigns = {}

    async def fake_activate(config, queue):
        return FakeSOCLead()

    monkeypatch.setattr("suijin.modules.blueteam.lib.blue.soc.soc_lead.activate_soc_lead", fake_activate)
    monkeypatch.setattr(
        "suijin.modules.blueteam.lib.blue.soc.tier1_analyst.create_tier1",
        lambda path: types.SimpleNamespace(endpoint=path),
    )
    monkeypatch.setattr(
        "suijin.modules.blueteam.lib.blue.soc.tier2_analyst.create_tier2", lambda: types.SimpleNamespace()
    )
    monkeypatch.setattr(
        "suijin.modules.blueteam.lib.blue.soc.threat_hunter.create_threat_hunter", lambda: types.SimpleNamespace()
    )
    monkeypatch.setattr(
        "suijin.modules.blueteam.lib.blue.soc.incident_commander.create_incident_commander",
        lambda: types.SimpleNamespace(),
    )

    # Proxy -> fake
    class FakeProxy:
        def stop(self):
            pass

    monkeypatch.setattr("suijin.modules.blueteam.lib.blue.proxy.start_proxy", lambda **kwargs: FakeProxy())
    mocks["proxy"] = FakeProxy

    # Subagent analyze -> empty (mock at class level)
    async def fake_analyze(self):
        return []

    monkeypatch.setattr(
        "suijin.modules.blueteam.lib.blue.subagent_manager.SubagentManager.analyze_all_endpoints", fake_analyze
    )

    # Isolate traffic log + lab port (don't touch real /tmp files)
    monkeypatch.setattr(bt, "BLUE_TRAFFIC_LOG", Path(str(tmp_path)) / "traffic.jsonl")
    monkeypatch.setattr(bt, "BLUE_LAB_PORT", 45999)
    mocks["tmpdir"] = str(tmp_path)

    return mocks


@pytest.fixture
def auto_quit_sleep(monkeypatch):
    """Make the monitoring loop exit on first iteration via /quit."""
    orig_sleep = asyncio.sleep

    def quit_on_sleep(delay):
        bt._signal._blue_interrupted = True
        return orig_sleep(0)

    monkeypatch.setattr(bt.asyncio, "sleep", quit_on_sleep)


class TestFindFreePort:
    def test_returns_bindable_port(self):
        import socket

        port = bt._find_free_port()
        s = socket.socket()
        try:
            s.bind(("", port))
        finally:
            s.close()
        assert isinstance(port, int)

    def test_custom_start(self):
        import socket

        # Find a free port then ask starting from it
        s = socket.socket()
        s.bind(("", 0))
        free = s.getsockname()[1]
        s.close()
        port = bt._find_free_port(start=free, max_attempts=5)
        assert port >= free

    def test_fallback_when_all_taken(self, monkeypatch):
        import socket

        class FakeSocket:
            def __init__(self):
                raise OSError("all ports busy")

            def settimeout(self, t):
                pass

        monkeypatch.setattr(socket, "socket", FakeSocket)
        assert bt._find_free_port() == bt.PROXY_DEFAULT_PORT


class TestMiddlewareSnippet:
    def test_prints_without_crash(self, monkeypatch):
        printed = []
        monkeypatch.setattr(bt.console, "print", lambda *a, **k: printed.append(a))
        bt._print_middleware_snippet(bt.console, "/tmp/test_log.jsonl")
        assert len(printed) == 1

    def test_contains_log_path(self):
        import io

        from rich.console import Console

        buf = io.StringIO()
        c = Console(file=buf, force_terminal=False)
        bt._print_middleware_snippet(c, "/tmp/custom_blue.jsonl")
        assert "/tmp/custom_blue.jsonl" in buf.getvalue()


class TestInitFirewall:
    def test_darwin_creates_pfctl_table(self, monkeypatch):
        calls = []

        class FakeResult:
            returncode = 0

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr(bt.console, "print", lambda *a, **k: None)
        bt._init_firewall(bt.console)
        assert len(calls) >= 2
        assert any("pfctl" in c[1] for c in calls)

    def test_linux_creates_iptables_chain(self, monkeypatch):
        calls = []

        class FakeResult:
            returncode = 0

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr(bt.console, "print", lambda *a, **k: None)
        bt._init_firewall(bt.console)
        assert len(calls) >= 1

    def test_pfctl_failure_handled(self, monkeypatch):
        def mock_run(cmd, **kwargs):
            raise Exception("pfctl not found")

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        printed = []
        monkeypatch.setattr(bt.console, "print", lambda text, **k: printed.append(text))
        bt._init_firewall(bt.console)
        assert any("unavailable" in str(p) for p in printed)


class TestRunAsyncBranches:
    """Drive _run_async with mocked inputs and dependencies."""

    def test_choice_3_returns(self, blue_mocks, monkeypatch):
        monkeypatch.setattr(bt.console, "input", _scripted_input(["3"]))
        asyncio.run(bt._run_async())

    def test_choice_1_invalid_path(self, blue_mocks, monkeypatch):
        monkeypatch.setattr(bt.console, "input", _scripted_input(["1", "/nonexistent/path"]))
        asyncio.run(bt._run_async())

    def test_choice_1_zero_port(self, blue_mocks, monkeypatch, tmp_path):
        monkeypatch.setattr(bt.console, "input", _scripted_input(["1", str(tmp_path), "0"]))
        asyncio.run(bt._run_async())

    def test_choice_1_full_flow_reaches_monitor(self, blue_mocks, monkeypatch, auto_quit_sleep, tmp_path):
        """Choice 1 with a valid path + port -> proxy starts -> monitor loop -> /quit."""
        monkeypatch.setattr(bt.console, "input", _scripted_input(["1", str(tmp_path), "8001", "/quit"]))
        monkeypatch.setattr(bt, "_find_free_port", lambda: 18080)
        asyncio.run(bt._run_async())
        assert blue_mocks["session"].endpoints_discovered == 1

    def test_choice_2_full_flow_reaches_monitor(self, blue_mocks, monkeypatch, auto_quit_sleep):
        """Choice 2 (built-in lab) -> mocked lab launch -> monitor loop -> /quit."""
        monkeypatch.setattr(bt.console, "input", _scripted_input(["2", "/quit"]))

        # Mock subprocess (lsof + Popen)
        class FakeProc:
            returncode = 0
            stdout = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakeProc())

        # Mock urlopen so the lab appears ready instantly
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ok"

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())

        asyncio.run(bt._run_async())
        assert blue_mocks["session"].endpoints_discovered == 1

    def test_eof_on_first_input_returns(self, blue_mocks, monkeypatch):
        monkeypatch.setattr(bt.console, "input", _scripted_input([]))
        asyncio.run(bt._run_async())

    def test_keyboard_interrupt_on_first_input(self, blue_mocks, monkeypatch):
        def raise_kb(prompt=""):
            raise KeyboardInterrupt()

        monkeypatch.setattr(bt.console, "input", raise_kb)
        asyncio.run(bt._run_async())


class TestMainEntry:
    def test_main_runs_coroutine(self, monkeypatch):
        ran = []

        async def fake_run():
            ran.append(True)

        monkeypatch.setattr(bt, "_run_async", fake_run)
        bt.main()
        assert ran == [True]


class TestEnvLoading:
    def test_env_file_loaded(self, blue_mocks, monkeypatch, tmp_path):
        """A .env next to BASE_DIR is loaded into os.environ.

        BASE_DIR is redirected to tmp_path so the real suijin/.env is never touched.
        """
        monkeypatch.setattr(bt, "BASE_DIR", Path(str(tmp_path)))
        env_file = Path(str(tmp_path)) / ".env"
        env_file.write_text("TEST_BLUE_VAR=loaded_value\n", encoding="utf-8")
        monkeypatch.setattr(bt.console, "input", _scripted_input(["3"]))
        try:
            asyncio.run(bt._run_async())
            assert os.environ.get("TEST_BLUE_VAR") == "loaded_value"
        finally:
            os.environ.pop("TEST_BLUE_VAR", None)
            env_file.unlink(missing_ok=True)

    def test_env_file_missing_no_crash(self, blue_mocks, monkeypatch, tmp_path):
        """Without .env, _run_async warns about the missing key but continues."""
        monkeypatch.setattr(bt, "BASE_DIR", Path(str(tmp_path)))
        monkeypatch.setattr(bt.console, "input", _scripted_input(["3"]))
        asyncio.run(bt._run_async())

    def test_env_comments_and_blanks_skipped(self, blue_mocks, monkeypatch, tmp_path):
        monkeypatch.setattr(bt, "BASE_DIR", Path(str(tmp_path)))
        env_file = Path(str(tmp_path)) / ".env"
        env_file.write_text("# comment\n\nREAL_VAR=yes\n", encoding="utf-8")
        monkeypatch.setattr(bt.console, "input", _scripted_input(["3"]))
        try:
            asyncio.run(bt._run_async())
            assert os.environ.get("REAL_VAR") == "yes"
        finally:
            os.environ.pop("REAL_VAR", None)
            env_file.unlink(missing_ok=True)
