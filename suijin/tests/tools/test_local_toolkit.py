"""The local toolkit — persistent shells, local recon, local exploitation,
ssh-reached-target ops. Read-only by contract; hermetic via _run pinning
where a live scan would touch the operator's real machine."""

import importlib.util
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[2] / "server" / "tools" / "lib"


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"lt_{name}", LAB / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def recon():
    return _mod("local_recon")


@pytest.fixture()
def ops():
    return _mod("local_ops")


class TestShellSessions:
    def test_state_persists_between_sends(self):
        s = _mod("shell_session")
        s._SESSIONS.clear()
        r = s.shell_start()
        sid = r.split()[1]
        try:
            out1 = s.shell_send(sid, "cd /tmp && export LT=1 && pwd")
            assert "[exit 0]" in out1 and "/tmp" in out1
            out2 = s.shell_send(sid, "pwd; echo $LT")
            assert "/tmp" in out2 and "LT=1" not in out2 and out2.rstrip().endswith("1")
        finally:
            s.shell_stop(sid)

    def test_bad_session_and_empty_cmd(self):
        s = _mod("shell_session")
        assert s.shell_send("nope", "ls").startswith("Error")
        r = s.shell_start()
        sid = r.split()[1]
        assert s.shell_send(sid, "").startswith("Error")
        s.shell_stop(sid)

    def test_stop_all_and_list(self):
        s = _mod("shell_session")
        s._SESSIONS.clear()
        s.shell_start()
        assert "shell session" in s.shell_list()
        assert "stopped 1" in s.shell_stop("all")


class TestLocalRecon:
    def test_sys_info_shape(self, recon):
        out = recon.local_sys_info()
        assert "host:" in out and "os:" in out and "arch:" in out

    def test_all_tools_never_raise_and_are_bounded(self, recon, monkeypatch):
        # hermetic: pin _run so nothing touches the real machine
        monkeypatch.setattr(recon, "_run", lambda cmd, timeout=15: "stub-output")
        for fn in (
            "local_sys_info",
            "local_priv_check",
            "local_services",
            "local_users",
            "local_sched",
            "local_cred_hunt",
            "local_net",
        ):
            out = getattr(recon, fn)()
            assert isinstance(out, str) and len(out) <= recon._CAP + 50

    def test_proc_filter(self, recon):
        out = recon.local_proc("launchd")
        assert "COMMAND" in out or "launchd" in out


class TestLocalOps:
    def test_lpe_scan_flags_findings(self, ops, monkeypatch):
        monkeypatch.setattr(
            ops,
            "_run",
            lambda cmd, timeout=20: (
                "User may run: (all) NOPASSWD: ALL" if "sudo" in str(cmd) and "-l" in str(cmd) else "stub"
            ),
        )
        out = ops.local_lpe_scan()
        assert "FINDINGS" in out and "sudo" in out.lower()

    def test_suid_audit_flags_abusable(self, ops, monkeypatch):
        monkeypatch.setattr(ops, "_run", lambda cmd, timeout=20: "/usr/bin/find\n/usr/bin/ptratest")
        # 'find' is GTFOBins-class — must be flagged
        out = ops.local_suid_audit()
        assert "ABUSABLE" in out and "!! /usr/bin/find" in out

    def test_docker_sock_absent(self, ops, monkeypatch):
        monkeypatch.setattr(ops, "_run", lambda cmd, timeout=20: "")
        out = ops.local_docker_sock()
        assert isinstance(out, str)  # absent socket path stays a clean string

    def test_ssh_validation(self, ops):
        assert ops.ssh_exec("", "ls").startswith("Error")
        assert ops.ssh_exec("host", "").startswith("Error")
        assert ops.ssh_pull("", "/x").startswith("Error")
        assert ops.ssh_push("host", "/no/such/file", "/x").startswith("Error")

    def test_file_find_requires_args(self, ops):
        assert ops.local_file_find().startswith("Error")


class TestDispatchWiring:
    def test_local_and_ssh_tools_route(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        assert "host:" in route_tool("local_sys_info", {}, {})
        assert route_tool("ssh_exec", {"host": "", "cmd": "x"}, {}).startswith("Error")
        assert "session" in route_tool("shell_list", {}, {})

    def test_tool_names_in_dispatch(self):
        import inspect

        import suijin.modules.tools.lib.dispatch as d

        src = inspect.getsource(d)
        for tool in (
            "local_lpe_scan",
            "local_suid_audit",
            "ssh_exec",
            "ssh_inventory",
            "shell_send",
            "local_hist_search",
        ):
            assert f'"{tool}"' in src, tool


class TestForceCompact:
    """/compact (trigger_chars=0) must always work: no refusal on message
    COUNT (10 giant tool outputs were uncompactable), honest no-op reasons."""

    def test_few_huge_messages_compact(self):
        from suijin.modules.agent.lib.compact import compact, history_chars

        msgs = [{"role": "system", "content": "sys"}] + [
            {"role": "assistant" if i % 2 else "user", "content": "X" * 30000} for i in range(10)
        ]
        out = compact(msgs, trigger_chars=0)
        assert out is not msgs, "300k chars in 10 messages must compact"
        assert history_chars(out) < history_chars(msgs) // 2

    def test_genuinely_tiny_conversation_stays(self):
        from suijin.modules.agent.lib.compact import compact

        msgs = [{"role": "system", "content": "sys"}] + [{"role": "user", "content": f"t{i}"} for i in range(4)]
        assert compact(msgs, trigger_chars=0) is msgs

    def test_normal_trigger_path_unchanged(self):
        from suijin.modules.agent.lib.compact import compact

        small = [{"role": "user", "content": "x" * 500}] * 10
        assert compact(small, trigger_chars=120_000) is small  # under trigger: no-op
        many = [{"role": "user", "content": "x" * 6000}] * 30
        out = compact(many, trigger_chars=120_000)
        assert out is not many

    def test_restart_rewires_set_state(self):
        """The provider-restart path must re-wire run_box._set_state onto
        the NEW thread (a forced /compact after restart was a silent
        no-op into the dead checkpoint)."""
        import inspect

        import suijin.modules.redteam.lib.redteamer as rt

        src = inspect.getsource(rt)
        start = src.find("one automatic restart")
        end = src.find("_restart_stream = True", start)
        assert start > 0 and end > start
        restart = src[start:end]
        assert "run_box._set_state" in restart, "restart path must re-wire the run box"
