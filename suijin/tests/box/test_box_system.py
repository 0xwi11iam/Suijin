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

        monkeypatch.setattr(shutil, "which", lambda n: n == "orb")
        assert bx.detect_engine() == "orb"

    def test_none_when_nothing(self, monkeypatch):
        monkeypatch.delenv("SUIJIN_CONTAINER_ENGINE", raising=False)
        import shutil

        monkeypatch.setattr(shutil, "which", lambda n: False)
        assert bx.detect_engine() == ""


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

        assert set(VERBS) == {"build", "start", "stop", "enter", "status", "refresh", "destroy"}


class TestProvisionResume:
    def test_tool_state_resumable(self, fake):
        from suijin.server.box import provision

        fake.files[".box-state.json"] = json.dumps({"stages": {"base": {"ok": True}}, "tools": {"nmap": {"ok": True}}})
        state = provision._state_read()
        assert state["stages"]["base"]["ok"] and state["tools"]["nmap"]["ok"]

    def test_build_stage_map(self):
        from suijin.server.box.provision import STAGES

        assert STAGES == ("base", "suijin", "profile", "config", "tools", "verify")


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
