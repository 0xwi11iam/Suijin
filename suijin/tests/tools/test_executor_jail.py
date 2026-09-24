"""Executor-stage write jail (confinement.py v2).

Process-level enforcement for shell redirection the tool-layer jail
cannot see: HOME/TMPDIR confinement always; OS-level write-deny via
sandbox-exec when installed (macOS) and executor_sandbox enabled.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from suijin.server.confinement import (
    _sandbox_profile,
    executor_env,
    sandbox_exec_available,
    wrap_exec,
)


@pytest.fixture
def jail_root(tmp_path):
    root = tmp_path / "engagement"
    (root / "home").mkdir(parents=True)
    return root


class TestExecutorEnv:
    def test_home_and_tmpdir_confined(self, jail_root):
        env = executor_env(root=jail_root)
        assert env["HOME"] == str(jail_root / "home")
        assert env["TMPDIR"] == str(jail_root / "home" / "tmp")

    def test_tmpdir_created(self, jail_root):
        executor_env(root=jail_root)
        assert (jail_root / "home" / "tmp").is_dir()

    def test_uncorrupted_paths(self, jail_root):
        env = executor_env(root=jail_root)
        assert "PATH" in env and "/bin" in env["PATH"]


class TestWrapExec:
    def test_inactive_without_strict(self, jail_root):
        argv, active = wrap_exec(["ls", "-l"], root=jail_root, strict=False)
        assert argv == ["ls", "-l"] and active is False

    def test_inactive_when_sandbox_missing(self, jail_root, monkeypatch):
        monkeypatch.setattr("suijin.server.confinement.sandbox_exec_available", lambda: False)
        argv, active = wrap_exec(["ls"], root=jail_root)
        assert argv == ["ls"] and active is False

    def test_active_on_darwin_with_sandbox(self, jail_root):
        if not sandbox_exec_available():
            pytest.skip("no sandbox-exec on this host")
        argv, active = wrap_exec(["ls"], root=jail_root)
        assert active is True
        assert argv[0] == "/usr/bin/sandbox-exec" and "-p" in argv[:3]

    def test_profile_denies_and_reamends(self, jail_root, tmp_path):
        prof = _sandbox_profile(root=jail_root, workspace=tmp_path)
        assert "(deny file-write*)" in prof
        assert f'(allow file-write* (subpath "{jail_root}"))' in prof
        assert '/private/tmp"' in prof  # canonical /tmp
        assert '(allow file-write* (literal "/dev/null"))' in prof


@pytest.mark.skipif(not sandbox_exec_available(), reason="no sandbox-exec on this host")
class TestSandboxExecE2E:
    """The profile really denies writes outside workspace+/tmp scratch and
    lets the engagement + scratch through."""

    def test_write_outside_denied(self, jail_root, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        denied = tmp_path / "elsewhere" / "illegal_probe"
        denied.parent.mkdir()
        env = executor_env(root=jail_root)
        r = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                _sandbox_profile(root=jail_root, workspace=ws),
                "/bin/sh",
                "-c",
                f"echo x > {denied}",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode != 0, f"write outside workspace should be denied: {r.stderr}"
        assert not denied.exists()

    def test_write_inside_engagement_allowed(self, jail_root, tmp_path):
        target = jail_root / "home" / "hit.txt"
        r = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                _sandbox_profile(root=jail_root, workspace=tmp_path),
                "/bin/sh",
                "-c",
                f"echo x > {target}",
            ],
            capture_output=True,
            text=True,
            env=executor_env(root=jail_root),
        )
        assert r.returncode == 0, r.stderr
        assert target.read_text() == "x\n"

    def test_workspace_and_tmp_scratch_allowed(self, jail_root, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        for p in (ws / "scratch.txt",):
            r = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-p",
                    _sandbox_profile(root=jail_root, workspace=ws),
                    "/bin/sh",
                    "-c",
                    f"echo y > {p}",
                ],
                capture_output=True,
                text=True,
                env=executor_env(root=jail_root),
            )
            assert r.returncode == 0, r.stderr
            assert p.read_text() == "y\n"

    def test_reads_planet_still_free(self, jail_root, tmp_path):
        r = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                _sandbox_profile(root=jail_root, workspace=tmp_path),
                "cat",
                "/etc/hosts",
            ],
            capture_output=True,
            text=True,
            env=executor_env(root=jail_root),
        )
        assert r.returncode == 0 and "localhost" in r.stdout


class TestExecuteTerminalWiring:
    """The live tool path: execute_terminal runs with HOME/TMPDIR into the
    engagement home, and (sandbox present) system writes are denied.

    The tool modules are imported through the RELOCATION ALIAS
    (suijin.modules.tools.lib.*) — the single spelling the whole tree
    uses. Deep canonical spellings (suijin.server.tools.lib.*) create a
    duplicate module object for the same package and can flip child
    imports; the runtime never does that (see architecture split note)."""

    @pytest.fixture
    def eng_ws(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        return tmp_path

    def test_home_and_pwd_confined(self, eng_ws):
        from suijin.modules.tools.lib.terminal import execute_terminal

        out = execute_terminal('echo hi; pwd; printf "%s" "$HOME"')
        assert "hi" in out
        assert (
            str(Path(str(eng_ws).removeprefix("/private")).resolve()) in out or str(Path(eng_ws).resolve()) in out
        )  # pwd + HOME both inside the engagement

    def test_sandbox_denies_system_write(self, eng_ws):
        if not sandbox_exec_available():
            pytest.skip("no sandbox-exec on this host")
        from suijin.modules.tools.lib.terminal import execute_terminal

        probe = "/var/tmp/suijin_jail_probe"
        out = execute_terminal(f"echo x > {probe}")
        assert not Path(probe).exists(), "jail failed: wrote outside workspace"
        assert "Operation not permitted" in out or "denied" in out.lower()

    def test_shell_session_home_confined_send(self, eng_ws):
        from suijin.modules.tools.lib.shell_session import shell_send, shell_start, shell_stop

        sid = shell_start().split()[1]  # "session sh_abcd (...) started..."
        try:
            out = shell_send(sid, "pwd; printf '%s' $HOME", timeout=10)
            assert str(Path(eng_ws).resolve()) in out
        finally:
            shell_stop(sid)

    def test_shell_jailed_marker(self, eng_ws):
        """When sandbox-exec is present the session banner advertises
        [jailed] so the model treats it as enforced, not guessed."""
        from suijin.modules.tools.lib.shell_session import shell_start, shell_stop

        banner = shell_start()
        sid = banner.split()[1]
        try:
            if sandbox_exec_available():
                from suijin.modules.platform.lib.config_loader import load_config

                assert ("jailed" in banner) == bool(load_config().get("executor_sandbox", True))
        finally:
            shell_stop(sid)
