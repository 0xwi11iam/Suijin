"""CLI-level tests for the v2.10 command verbs: kb, pull cve, creds,
dossier, timeline, watch, clean, rules, policy, providers, module, notify.

Arg-parsing, exit codes, and output shape — the underlying libraries have
their own tests; these pin the CLI surface so refactorings can't silently
break verbs.
"""

import json

import pytest

from medusa import cli
from medusa.tests.test_cli_commands import run_cli


class TestKbVerbs:
    def test_kb_diff_not_built(self, monkeypatch):
        import medusa.kb as kbmod

        monkeypatch.setattr(kbmod, "kb_diff", lambda: {"built": False, "sources": {}})
        code, out = run_cli(["kb", "diff"])
        assert code == 1
        assert "NOT BUILT" in out

    def test_kb_diff_ok(self, monkeypatch):
        import medusa.kb as kbmod

        monkeypatch.setattr(
            kbmod,
            "kb_diff",
            lambda: {
                "built": True,
                "built_at": "2026-08-18T00:00:00+00:00",
                "sources": {
                    "hacktricks": {"cached": True, "indexed_docs": 900, "cache_newer_than_build": False, "action": "ok"}
                },
            },
        )
        code, out = run_cli(["kb", "diff"])
        assert code == 0
        assert "hacktricks" in out and "900" in out
        assert "up to date" in out

    def test_kb_diff_flags_stale(self, monkeypatch):
        import medusa.kb as kbmod

        monkeypatch.setattr(
            kbmod,
            "kb_diff",
            lambda: {
                "built": True,
                "built_at": "2026-08-18T00:00:00+00:00",
                "sources": {
                    "gtfobins": {
                        "cached": True,
                        "indexed_docs": 400,
                        "cache_newer_than_build": True,
                        "action": "rebuild",
                    }
                },
            },
        )
        code, out = run_cli(["kb", "diff"])
        assert code == 0
        assert "rebuild" in out and "--sources gtfobins" in out

    def test_kb_read_missing_doc(self, monkeypatch):
        import medusa.kb as kbmod

        def boom(path, source=None):
            raise FileNotFoundError("no match")

        monkeypatch.setattr(kbmod, "read_doc", boom)
        code, out = run_cli(["kb", "read", "zzz"])
        assert code == 1
        assert "error" in out

    def test_kb_read_dumps_content(self, monkeypatch):
        import medusa.kb as kbmod

        monkeypatch.setattr(kbmod, "read_doc", lambda p, source=None: ("gtfobins", p, "FULL CONTENT"))
        code, out = run_cli(["kb", "read", "_gtfobins/awk"])
        assert code == 0
        assert "[gtfobins] _gtfobins/awk" in out
        assert "FULL CONTENT" in out


class TestPullCve:
    def test_status_without_mirror(self, monkeypatch):
        import medusa.tools.cve_mirror as cm

        monkeypatch.setattr(cm, "KEV_PATH", type("P", (), {"exists": lambda s: False})())
        code, out = run_cli(["pull", "cve", "--status"])
        assert code == 1
        assert "not built" in out

    def test_pull_network_failure(self, monkeypatch):
        import medusa.tools.cve_mirror as cm

        def boom(force=False):
            raise RuntimeError("no network")

        monkeypatch.setattr(cm, "pull_kev", boom)
        code, out = run_cli(["pull", "cve"])
        assert code == 1


class TestCreds:
    def test_list_without_vault(self, monkeypatch):
        from medusa.tools import credential_vault as vault

        monkeypatch.setattr(vault, "VAULT_PATH", type("P", (), {"exists": lambda s: False})())
        code, out = run_cli(["creds", "list"])
        assert code == 1
        assert "No vault" in out

    def test_init_list_get_roundtrip(self, monkeypatch, tmp_path):
        import getpass

        from medusa.tools import credential_vault as vault

        monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "v.json")
        monkeypatch.setattr(vault, "LEGACY_PATH", tmp_path / "legacy.json")
        monkeypatch.setattr(getpass, "getpass", lambda *a: "pw")

        code, out = run_cli(["creds", "init"])
        assert code == 0 and "initialized" in out
        code, out = run_cli(["creds", "add", "--service", "web", "--value", "s3cret"])
        assert code == 0 and "Stored" in out
        code, out = run_cli(["creds", "list"])
        assert code == 0 and "web" in out and "s3cret" not in out  # hidden
        code, out = run_cli(["creds", "list", "--reveal"])
        assert "s3cret" in out
        code, out = run_cli(["creds", "get", "web"])
        assert "s3cret" in out
        code, out = run_cli(["creds", "get", "nomatch"])
        assert "No credentials matching" in out

    def test_export_redacted(self, monkeypatch, tmp_path):
        import getpass

        from medusa.tools import credential_vault as vault

        monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "v.json")
        monkeypatch.setattr(vault, "LEGACY_PATH", tmp_path / "legacy.json")
        monkeypatch.setattr(getpass, "getpass", lambda *a: "pw")
        run_cli(["creds", "init"])
        run_cli(["creds", "add", "--service", "api", "--value", "sk-live-9"])
        code, out = run_cli(["creds", "export"])
        assert code == 0 and "REDACTED" in out
        # default export lands in the real workspace reports dir — verify via
        # the returned message instead of touching the real workspace
        assert "exported" in out


class TestDossier:
    def test_missing_target_is_arg_error(self):
        with pytest.raises(SystemExit) as ei:
            cli.main(["dossier"])
        assert ei.value.code == 2

    def test_dossier_renders(self, monkeypatch):
        from medusa.tools import dossier as dos

        monkeypatch.setattr(
            dos,
            "build_dossier",
            lambda t: {"target": t, "constraints": {}, "failures": [], "engagements": [], "reports": []},
        )
        monkeypatch.setattr(dos, "render_dossier", lambda d: f"RENDERED {d['target']}")
        code, out = run_cli(["dossier", "example.com"])
        assert code == 0 and "RENDERED example.com" in out


class TestTimelineWatchClean:
    def test_timeline_empty(self, monkeypatch):
        from medusa.tools import housekeeping as hk

        monkeypatch.setattr(hk, "build_timeline", lambda limit=60: [])
        code, out = run_cli(["timeline"])
        assert code == 0 and "No engagement history" in out

    def test_timeline_groups_days(self, monkeypatch):
        from medusa.tools import housekeeping as hk

        monkeypatch.setattr(
            hk,
            "build_timeline",
            lambda limit=60: [
                {"ts": "2026-08-18 01:00:00", "kind": "engagement start", "detail": "x"},
                {"ts": "2026-08-18 02:00:00", "kind": "session saved", "detail": "y"},
            ],
        )
        code, out = run_cli(["timeline"])
        assert code == 0
        assert "2026-08-18" in out
        assert "engagement start" in out and "session saved" in out

    def test_watch_missing_log(self, monkeypatch, tmp_path):
        code, out = run_cli(["watch", "--traffic", str(tmp_path / "nope.jsonl")])
        assert code == 1 and "No traffic log" in out

    def test_watch_processes_then_stops(self, monkeypatch, tmp_path):
        from medusa.tools import housekeeping as hk

        log = tmp_path / "t.jsonl"
        log.write_text(json.dumps({"timestamp": "2026-08-18T10:00:00", "method": "GET", "path": "/"}) + "\n")

        def fake_tail(path, poll=0.5):
            yield log.read_text().strip()
            raise KeyboardInterrupt

        monkeypatch.setattr(hk, "tail_file", fake_tail)
        code, out = run_cli(["watch", "--traffic", str(log)])
        assert code == 0 and "stopped" in out

    def test_clean_dry_run_vs_apply(self, monkeypatch, tmp_path):
        import time as _time

        from medusa.tools import housekeeping as hk

        outputs = tmp_path / "outputs"
        outputs.mkdir()
        stale = outputs / "old.log"
        stale.write_text("x")
        old = _time.time() - 40 * 86400
        import os

        os.utime(stale, (old, old))
        monkeypatch.setattr(hk, "WORKSPACE_DIR", tmp_path)

        code, out = run_cli(["clean"])
        assert code == 0 and "dry-run" in out and stale.exists()

        code, out = run_cli(["clean", "--apply", "--days", "30"])
        assert code == 0 and "archived" in out and not stale.exists()


class TestRulesPolicy:
    def test_rules_no_file(self, monkeypatch, tmp_path):
        from medusa.tools import governance as gov

        monkeypatch.setattr(gov, "RULES_PATH", tmp_path / "rules.json")
        code, out = run_cli(["rules", "validate"])
        assert code == 0 and "no rules file" in out

    def test_rules_bad_regex_exits_1(self, monkeypatch, tmp_path):
        from medusa.tools import governance as gov

        rf = tmp_path / "rules.json"
        rf.write_text(json.dumps([{"name": "x", "pattern": "(bad"}]))
        monkeypatch.setattr(gov, "RULES_PATH", rf)
        code, out = run_cli(["rules", "validate"])
        assert code == 1 and "bad regex" in out

    def test_rules_list(self, monkeypatch, tmp_path):
        from medusa.tools import governance as gov

        rf = tmp_path / "rules.json"
        rf.write_text(json.dumps([{"name": "probe", "pattern": "/x", "field": "path", "weight": 4}]))
        monkeypatch.setattr(gov, "RULES_PATH", rf)
        code, out = run_cli(["rules", "list"])
        assert code == 0 and "probe" in out

    def test_policy_no_file_hints(self, monkeypatch, tmp_path):
        from medusa.tools import governance as gov

        monkeypatch.setattr(gov, "POLICY_PATH", tmp_path / "policy.json")
        code, out = run_cli(["policy", "check"])
        assert code == 0 and "no policy file" in out

    def test_policy_show_and_check(self, monkeypatch, tmp_path):
        from medusa.tools import governance as gov

        pf = tmp_path / "policy.json"
        pf.write_text(json.dumps({"allowed_target_scopes": ["127.0.0.1"]}))
        monkeypatch.setattr(gov, "POLICY_PATH", pf)
        code, out = run_cli(["policy", "check"])
        assert code == 0 and "valid" in out
        code, out = run_cli(["policy", "show"])
        assert "127.0.0.1" in out


class TestProvidersProbe:
    def test_skip_without_key(self, monkeypatch):

        from medusa.core.red import config_loader

        monkeypatch.setattr(config_loader, "load_config", lambda: {"provider": "zai"})
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        code, out = run_cli(["providers"])
        assert code == 1 and "SKIP" in out

    def test_ok_with_mocked_generate(self, monkeypatch):

        from medusa.core.red import config_loader
        from medusa.tools import providers

        monkeypatch.setattr(config_loader, "load_config", lambda: {"provider": "zai"})
        monkeypatch.setenv("ZAI_API_KEY", "k")
        monkeypatch.setattr(providers, "generate", lambda msgs, cfg, **kw: "pong")
        code, out = run_cli(["providers"])
        assert code == 0 and "ok" in out and "1/1" in out

    def test_failure_reported(self, monkeypatch):

        from medusa.core.red import config_loader
        from medusa.tools import providers

        monkeypatch.setattr(config_loader, "load_config", lambda: {"provider": "zai"})
        monkeypatch.setenv("ZAI_API_KEY", "k")
        monkeypatch.setattr(providers, "generate", lambda msgs, cfg, **kw: "Error: dead")
        code, out = run_cli(["providers"])
        assert code == 1 and "FAIL" in out


class TestModuleNotify:
    def test_module_init_and_validate(self, monkeypatch, tmp_path):
        from medusa.tools import module_sdk

        monkeypatch.setattr(module_sdk, "MODULES_ROOT", tmp_path)
        code, out = run_cli(["module", "init", "smoke_pack"])
        assert code == 0 and "scaffolded" in out
        assert (tmp_path / "smoke_pack" / "manifest.json").exists()
        code, out = run_cli(["module", "validate", "smoke_pack"])
        assert code == 0 and "valid" in out

    def test_module_init_rejects_duplicate(self, monkeypatch, tmp_path):
        from medusa.tools import module_sdk

        monkeypatch.setattr(module_sdk, "MODULES_ROOT", tmp_path)
        run_cli(["module", "init", "dup"])
        code, out = run_cli(["module", "init", "dup"])
        assert code == 1 and "error" in out

    def test_notify_send_file_channel(self, monkeypatch, tmp_path):
        from medusa.tools import notify

        monkeypatch.setattr(notify, "load_config", lambda: {"file": str(tmp_path / "n.log")})
        code, out = run_cli(["notify", "send", "engagement", "done"])
        assert code == 0 and "appended" in out
        assert "done" in (tmp_path / "n.log").read_text()

    def test_notify_send_requires_message(self):
        with pytest.raises(SystemExit) as ei:
            cli.main(["notify", "send"])
        assert ei.value.code == 2

    def test_notify_test_with_example_config(self, monkeypatch, tmp_path):
        from medusa.tools import notify

        monkeypatch.setattr(notify, "load_config", lambda: {})
        monkeypatch.setattr(notify, "CONFIG_PATH", tmp_path / "notify.json")
        code, out = run_cli(["notify", "test"])
        assert code == 0 and "example config" in out
