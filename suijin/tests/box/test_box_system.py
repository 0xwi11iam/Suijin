"""box system tests — engine detection, sync drift, provision lines,
cli routing, and the tui's pure render helpers. No container runtime
is required: the engine layer is patched to a fake for every test.
"""

import json

import pytest

from suijin.server.box import engine as bx
from suijin.server.box import sync as bsync


class FakeEngine:
    """In-memory box: exists/running + a file store for manifests."""

    def __init__(self):
        self.exists = False
        self.running = False
        self.files: dict[str, str] = {}
        self.cmds: list[str] = []

    # patch targets
    def box_exists(self, eng=None):
        return self.exists

    def box_running(self, eng=None):
        return self.running

    def run_in_box(self, cmd, timeout=600, engine=None):
        self.cmds.append(cmd)
        if cmd.startswith("cat /opt/suijin-ws/"):
            key = cmd.split("/opt/suijin-ws/")[-1].split(" ")[0]
            return (0, self.files.get(key, "")) if key in self.files else (1, "")
        if cmd.startswith("printf"):
            import re

            m = re.search(r"> /opt/suijin-ws/(\S+)", cmd)
            body = re.search(r"printf '%s' '([^']*)'", cmd)
            if m and body:
                self.files[m.group(1)] = body.group(1)
                return 0, ""
        return 0, ""


@pytest.fixture()
def fake(monkeypatch):
    f = FakeEngine()
    monkeypatch.setattr(bx, "box_exists", f.box_exists)
    monkeypatch.setattr(bx, "box_running", f.box_running)
    monkeypatch.setattr(bx, "run_in_box", f.run_in_box)
    monkeypatch.setattr(bsync, "read_box_file", lambda p: f.files.get(p, ""))
    return f


class TestEngineDetect:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("SUIJIN_CONTAINER_ENGINE", "docker")
        assert bx.detect_engine() == "docker"

    def test_autodetect_orb_first(self, monkeypatch):
        monkeypatch.delenv("SUIJIN_CONTAINER_ENGINE", raising=False)
        import shutil
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(shutil, "which", lambda n: n == "orb")
        assert bx.detect_engine() == "orb"

    def test_none_when_nothing(self, monkeypatch):
        monkeypatch.delenv("SUIJIN_CONTAINER_ENGINE", raising=False)
        import shutil
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(shutil, "which", lambda n: False)
        assert bx.detect_engine() == ""

    def test_linux_never_sees_orb_and_is_never_asked(self, monkeypatch):
        """Full-compatibility rule (2026-10-24): orbstack is mac-only.
        A linux user with docker gets docker SILENTLY — no prompt, even
        with both binaries oddly on PATH and a TTY."""
        import shutil
        import sys

        monkeypatch.delenv("SUIJIN_CONTAINER_ENGINE", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(shutil, "which", lambda n: n in ("orb", "docker"))
        monkeypatch.setattr(bx, "_configured_engine", lambda: "")
        prompted: list[str] = []
        monkeypatch.setattr(bx, "_prompt_engine", lambda: (prompted.append("x"), "")[1])
        monkeypatch.setattr(bx, "_stdin_is_tty", lambda: True)
        assert bx.detect_engine(interactive=True) == "docker"
        assert not prompted

    def test_docker_exec_gets_minus_i_for_stdin_streams(self, monkeypatch):
        monkeypatch.setattr(bx, "detect_engine", lambda interactive=True: "docker")
        argv = bx.box_cmd_argv("tar xzf - -C /opt", stdin_attached=True)
        assert argv[:4] == ["docker", "exec", "-i", "suijin-box"]
        plain = bx.box_cmd_argv("true")
        assert "-i" not in plain

    def test_docker_create_caps_resources_and_mounts_conditionally(self, monkeypatch):
        captured: dict[str, list] = {}

        def fake_run(cmd, timeout=600):
            captured["argv"] = cmd
            return 0, "container-id"

        monkeypatch.setattr(bx, "detect_engine", lambda interactive=True: "docker")
        monkeypatch.setattr(bx, "_run", fake_run)
        monkeypatch.setattr(bx, "repo_root", lambda: "/home/user/src/suijin")
        ok, _ = bx.box_create()
        assert ok and "--memory" in captured["argv"] and "--cpus" in captured["argv"]
        assert "-v" in captured["argv"]
        monkeypatch.setattr(bx, "repo_root", lambda: "")  # pip-installed: no tree
        ok, _ = bx.box_create()
        assert ok and "-v" not in captured["argv"]


class TestDrift:
    def test_no_drift_when_manifests_match(self, fake, tmp_path, monkeypatch):
        monkeypatch.setattr(bsync, "_ws_dir", lambda: tmp_path)
        (tmp_path / "prompt.md").write_text("v1")
        host = bsync.host_manifest()
        fake.files[".sync-manifest.json"] = json.dumps({"synced": host})
        fake.exists = fake.running = True
        monkeypatch.setattr(bsync, "tool_list", lambda: [])
        d = bsync.drift()
        assert d["changed"] == [] and d["missing_tools"] == []

    def test_changed_item_detected(self, fake, tmp_path, monkeypatch):
        monkeypatch.setattr(bsync, "_ws_dir", lambda: tmp_path)
        (tmp_path / "prompt.md").write_text("v1")
        fake.files[".sync-manifest.json"] = json.dumps({"synced": {"prompt.md": "oldhash"}})
        fake.exists = fake.running = True
        monkeypatch.setattr(bsync, "tool_list", lambda: [])
        d = bsync.drift()
        assert "prompt.md" in d["changed"]

    def test_missing_tools_use_declared_presence(self, fake, tmp_path, monkeypatch):
        """command -v falsely reports python-module deps (requests) as
        missing; drift must use the declared checks."""
        monkeypatch.setattr(bsync, "_ws_dir", lambda: tmp_path)
        (tmp_path / "prompt.md").write_text("v1")
        fake.files[".sync-manifest.json"] = json.dumps({"synced": bsync.host_manifest()})
        fake.exists = fake.running = True
        monkeypatch.setattr(bsync, "tool_list", lambda: ["requests", "testssl.sh"])
        seen: dict[str, str] = {}

        def fake_run(cmd, timeout=600, engine=None):
            seen["cmd"] = cmd
            return 0, "requests ok\ntestssl.sh missing"

        monkeypatch.setattr(bx, "run_in_box", fake_run)
        d = bsync.drift()
        assert "import requests" in seen["cmd"]
        assert "command -v testssl" in seen["cmd"]
        assert d["missing_tools"] == ["testssl.sh"]


class TestConfigSync:
    def test_env_is_a_sync_item(self):
        assert ".env" in bsync._SYNC_ITEMS

    def test_env_source_is_the_workspace_env(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / ".env").write_text("OPENCODE_API_KEY=x\n")
        monkeypatch.delenv("SUIJIN_ENV", raising=False)
        monkeypatch.setattr(bsync, "_ws_dir", lambda: ws)
        assert bsync._env_source() == ws / ".env"

    def test_profile_points_the_loader_at_the_workspace(self, fake, monkeypatch):
        from suijin.server.box import engine as bx2
        from suijin.server.box import provision as prov

        seen: dict[str, str] = {}

        def fake_run(cmd, timeout=600, engine=None):
            seen[cmd] = cmd
            return 0, ""

        monkeypatch.setattr(bx2, "run_in_box", fake_run)
        assert prov.stage_profile(lambda _m: None)
        profile_cmd = next(c for c in seen if "profile.d" in c)
        assert "SUIJIN_CONFIG=/opt/suijin-ws/config.json" in profile_cmd
        assert "SUIJIN_ENV=/opt/suijin-ws/.env" in profile_cmd


class TestToolLines:
    def test_apt_line_from_doctor_table(self):
        from suijin.server.box.provision import _tool_apt_line

        line = _tool_apt_line("nmap")
        assert "nmap" in line and ("apt" in line or "install" in line)

    def test_unknown_tool_falls_back(self):
        from suijin.server.box.provision import _tool_apt_line

        assert "warpdrive" in _tool_apt_line("warpdrive")

    def test_default_tool_list_from_doctor(self):
        tools = bsync.tool_list()
        assert "nmap" in tools and "nuclei" in tools


class TestCliRouting:
    def test_unknown_verb(self, capsys):
        from suijin.server.box.cli import run_box_cli

        rc = run_box_cli(["warp"])
        assert rc == 2
        assert "unknown box command" in capsys.readouterr().out

    def test_status_without_engine(self, monkeypatch, capsys):
        monkeypatch.setattr(bx, "detect_engine", lambda: "")
        from suijin.server.box.cli import run_box_cli

        assert run_box_cli(["status"]) == 1
        assert "no container engine" in capsys.readouterr().out

    def test_verbs_registered(self):
        from suijin.server.box.cli import VERBS

        assert set(VERBS) == {"build", "start", "stop", "enter", "shell", "status", "refresh", "destroy"}


class TestProvisionResume:
    def test_tool_state_resumable(self, fake):
        from suijin.server.box import provision

        fake.files[".box-state.json"] = json.dumps({"stages": {"base": {"ok": True}}, "tools": {"nmap": {"ok": True}}})
        state = provision._state_read()
        assert state["stages"]["base"]["ok"] and state["tools"]["nmap"]["ok"]

    def test_build_stage_map(self):
        from suijin.server.box.provision import STAGES

        assert STAGES == ("base", "suijin", "profile", "config", "tools", "verify")

    def test_profile_and_config_never_resume_skip(self):
        from suijin.server.box.provision import _stage_resumable

        assert not _stage_resumable("profile")
        assert not _stage_resumable("config")
        assert _stage_resumable("base")


class TestTuiRender:
    def test_bar_blocks_and_ascii(self):
        from suijin.server.box.tui import _bar

        b = _bar(0.5, 10)
        assert len(b) == 10 and "█" in b and "░" in b
        a = _bar(0.5, 10, ascii_only=True)
        assert a == "#####-----"

    def test_bar_clamps(self):
        from suijin.server.box.tui import _bar

        assert _bar(-1, 6).startswith("░")
        assert _bar(2, 6).endswith("█")


class TestVerbRegistered:
    def test_box_in_known_verbs(self):
        from suijin.modules.console.lib.cli import _KNOWN_VERBS, is_known_verb

        assert "box" in _KNOWN_VERBS and is_known_verb("box")
        assert "container" in _KNOWN_VERBS

    def test_shell_verb_registered(self):
        from suijin.server.box.cli import VERBS

        assert "shell" in VERBS


class TestCleanRowsAndCompat:
    """Field 2026-10-24: apt's pty progress redraws shredded the live
    build stream into diagonal scattered rows; and docker/root boxes
    ship without sudo. Both fixed for every engine."""

    def test_apt_becomes_quiet_pipe_mode_apt_get(self):
        from suijin.server.box.provision import _box_line

        out = _box_line("sudo apt install --no-install-recommends -y nmap")
        assert out.startswith("sudo apt-get install")
        assert "-q" in out and "Dpkg::Use-Pty=0" in out
        assert "nmap" in out

    def test_clean_stream_line_collapses_redraws(self):
        from suijin.server.box.provision import _clean_stream_line

        assert _clean_stream_line("Progress: [ 5%]\rProgress: [ 55%]\rProgress: [100%]") == "Progress: [100%]"
        assert _clean_stream_line("(Reading database ... 5%\x08\x08\x08 10%)") == "(Reading database ... 5% 10%)"
        assert "    " not in _clean_stream_line("a    b        c")

    def test_base_bootstrap_survives_root_without_sudo(self):
        """docker's kali image runs as root with no sudo binary — the
        bootstrap must adapt with $S instead of dying on `sudo:`."""
        import inspect

        from suijin.server.box import provision

        src = inspect.getsource(provision.stage_base)
        assert "command -v sudo" in src and "$S apt-get" in src

    def test_profile_writes_bashrc_too(self, fake, monkeypatch):
        """Tools must be on PATH in EVERY shell: profile.d covers login
        shells (our exec paths), .bashrc covers the interactive `box
        shell` session (operator ruling 2026-10-24)."""
        from suijin.server.box import engine as bx2
        from suijin.server.box import provision as prov

        cmds: list[str] = []

        def fake_run(cmd, timeout=600, engine=None):
            cmds.append(cmd)
            return 0, ""

        monkeypatch.setattr(bx2, "run_in_box", fake_run)
        assert prov.stage_profile(lambda _m: None)
        assert any("profile.d" in c for c in cmds)
        assert any(".bashrc" in c and "suijin-box path" in c for c in cmds)
