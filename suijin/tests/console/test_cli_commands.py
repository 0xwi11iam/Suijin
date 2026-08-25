"""Tests for the non-interactive CLI verbs: status, version, env, tools,
modules, skills, config show/validate, workspace, reports, sessions, labs.

Every command must be offline, scriptable, and exit 0 on success.
"""

import json

import pytest

from suijin.modules.console.lib import cli


def run_cli(argv):
    """Invoke cli.main and return (exit_code, stdout)."""
    import contextlib
    import io

    buf = io.StringIO()
    with pytest.raises(SystemExit) as ei, contextlib.redirect_stdout(buf):
        cli.main(argv)
    return ei.value.code, buf.getvalue()


class TestSimpleVerbs:
    def test_version(self):
        code, out = run_cli(["version"])
        assert code == 0
        assert "suijin" in out
        assert "python" in out
        assert "package:" in out

    def test_status(self):
        code, out = run_cli(["status"])
        assert code == 0
        assert "knowledge base" in out
        assert "workspace:" in out
        assert "modules:" in out
        assert "lab port" in out

    def test_status_reports_zai_endpoint(self, monkeypatch, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"provider": "zai", "zai_endpoint": "paas"}))
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "_has_any_api_key", lambda _p: True)
        code, out = run_cli(["status"])
        assert code == 0
        assert "provider:         zai" in out
        assert "endpoint: paas" in out

    def test_tools_lists_core_and_module_tools(self):
        code, out = run_cli(["tools"])
        assert code == 0
        assert "Core tools (" in out
        assert "search_kb" in out
        assert "execute_terminal" in out
        assert "Module tools:" in out

    def test_modules_lists_packs(self):
        code, out = run_cli(["modules"])
        assert code == 0
        assert "module packs:" in out
        assert "nmap" in out  # v4.1: flat pack keys
        assert "tools total" in out

    def test_skills_lists_skills(self):
        code, out = run_cli(["skills"])
        assert code == 0
        assert "blue_recon" in out

    def test_labs_lists_real_labs(self):
        code, out = run_cli(["labs"])
        assert code == 0
        # every real lab directory is present with its actual port
        assert "blue_target" in out and ":5906" in out
        assert "devops_dashboard" in out and ":5700" in out
        assert "oauth_lab" in out and ":5902" in out
        assert "python3 suijin/lab/" in out

    def test_workspace_status(self):
        code, out = run_cli(["workspace"])
        assert code == 0
        assert "workspace:" in out
        assert "symlink:" in out


class TestEnvCommand:
    def test_set_and_unset_keys(self, monkeypatch):
        monkeypatch.setenv("ZAI_API_KEY", "super-secret-value-xyz")
        monkeypatch.delenv("HF_TOKEN", raising=False)
        code, out = run_cli(["env"])
        assert code == 0
        assert "ZAI_API_KEY" in out and "SET" in out
        assert "HF_TOKEN" in out and "not set" in out
        # values are NEVER printed
        assert "super-secret-value-xyz" not in out

    def test_reads_suijin_dotenv(self, monkeypatch, tmp_path):
        (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-file-secret-123\n")
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        code, out = run_cli(["env"])
        assert code == 0
        assert "DEEPSEEK_API_KEY" in out and "SET" in out and "suijin/.env" in out
        assert "sk-file-secret-123" not in out


class TestConfigCommands:
    def test_show_redacts_secrets(self, monkeypatch):
        monkeypatch.setattr(
            cli,
            "_effective_config",
            lambda: {
                "provider": "zai",
                "api_key": "sk-live-123",
                "nested": {"hf_token": "tok-abc"},
                "temperature": 0.4,
            },
        )
        code, out = run_cli(["config", "show"])
        assert code == 0
        assert "sk-live-123" not in out
        assert "tok-abc" not in out
        assert "***redacted***" in out
        assert '"provider": "zai"' in out

    def test_validate_ok(self, monkeypatch, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({"provider": "zai", "zai_endpoint": "coding"}))
        (tmp_path / "blue_config.json").write_text("{}")
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        from suijin.modules.blueteam.lib.blue import config as _blue_cfg

        monkeypatch.setattr(_blue_cfg, "CONFIG_PATH", tmp_path / "blue_config.json")  # v4.1: workspace home
        code, out = run_cli(["config", "validate"])
        assert code == 0
        assert "[ok] config.json: valid" in out
        assert "[ok] blue_config.json: valid" in out

    def test_validate_rejects_bad_zai_endpoint(self, monkeypatch, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({"zai_endpoint": "free-tier"}))
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        code, out = run_cli(["config", "validate"])
        assert code == 1
        assert "INVALID" in out

    def test_validate_rejects_bad_types(self, monkeypatch, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({"temperature": "hot"}))
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        code, out = run_cli(["config", "validate"])
        assert code == 1

    def test_missing_files_are_skipped_not_errors(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        code, out = run_cli(["config", "validate"])
        assert code == 0
        assert "not present" in out

    def test_bare_config_shows_help(self):
        with pytest.raises(SystemExit) as ei:
            cli.main(["config"])
        assert ei.value.code == 2


class TestArtifactListings:
    """reports / sessions read the canonical workspace — tmp-backed here."""

    def test_reports_listing(self, monkeypatch, tmp_path):
        import suijin.modules.platform.lib.workspace as ws

        reports = tmp_path / "outputs" / "reports"
        reports.mkdir(parents=True)
        (reports / "eng_report.md").write_text("# report")
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        code, out = run_cli(["reports"])
        assert code == 0
        assert "eng_report.md" in out

    def test_reports_empty(self, monkeypatch, tmp_path):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        code, out = run_cli(["reports"])
        assert code == 0
        assert "No reports yet" in out

    def test_sessions_listing_shows_objective(self, monkeypatch, tmp_path):
        import suijin.modules.platform.lib.workspace as ws

        sessions = tmp_path / "outputs" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "s1.json").write_text(json.dumps({"objective": "own the lab"}))
        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        code, out = run_cli(["sessions"])
        assert code == 0
        assert "s1.json" in out
        assert "own the lab" in out

    def test_sessions_empty(self, monkeypatch, tmp_path):
        import suijin.modules.platform.lib.workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        code, out = run_cli(["sessions"])
        assert code == 0
        assert "No saved sessions" in out


class TestDoctorWorkspaceRow:
    def test_doctor_includes_workspace_row(self, monkeypatch, capsys):
        monkeypatch.setattr(cli.shutil, "which", lambda _b: "/bin/true")
        monkeypatch.setattr(cli, "REQUIRED_BINARIES", [])
        monkeypatch.setattr(cli, "_importable", lambda _m: True)
        monkeypatch.setattr(cli, "_port_free", lambda _p: True)
        monkeypatch.setattr(cli, "_has_any_api_key", lambda _p: False)
        code = cli.run_doctor()
        out = capsys.readouterr().out
        assert code == 0
        assert "workspace" in out
        assert "symlink ok" in out

    def test_doctor_pack_dep_sweep_shows_hints(self, monkeypatch, capsys):
        """The manifest-driven sweep: every pack dep checked, misses get
        the OS-tailored install command, coverage row summarises."""
        from suijin.modules.tools.lib import availability as av

        monkeypatch.setattr(cli.shutil, "which", lambda _b: "/bin/true")
        monkeypatch.setattr(cli, "REQUIRED_BINARIES", [])
        monkeypatch.setattr(cli, "_importable", lambda _m: True)
        monkeypatch.setattr(cli, "_port_free", lambda _p: True)
        monkeypatch.setattr(cli, "_has_any_api_key", lambda _p: False)
        monkeypatch.setattr(av, "binary_status", lambda: {"hashcat": False, "made-up-dep": True})
        monkeypatch.setattr(av, "install_hint", lambda b: f"brew install {b}")
        code = cli.run_doctor()
        out = capsys.readouterr().out
        assert code == 0  # pack-dep misses are WARNs, not criticals
        assert "tool/hashcat" in out and "brew install hashcat" in out
        assert "tool/made-up-dep" not in out  # available deps stay silent
        assert "1/2 pack dependencies available" in out
        assert "packs (vendored" in out  # the split is visible
        assert "addons" in out


class TestHelpers:
    def test_redact_nested_and_lists(self):
        red = cli._redact({"api_key": "x", "keep": 1, "l": [{"password": "p"}]})
        assert red["api_key"] == "***redacted***"
        assert red["keep"] == 1
        assert red["l"][0]["password"] == "***redacted***"

    def test_redact_keeps_empty_values(self):
        assert cli._redact({"api_key": ""})["api_key"] == ""
