"""Tests for v2.10 operator features: credential vault, dossier, notify,
governance (rules + policy), module SDK, provider failover, skill
versioning, housekeeping (campaign/watch/timeline/clean), recon hook."""

import json
import time
from pathlib import Path

import pytest

# ── Credential vault ───────────────────────────────────────────────────


class TestVault:
    @pytest.fixture
    def vault_env(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import credential_vault as vault

        monkeypatch.setattr(vault, "VAULT_PATH", tmp_path / "credentials.vault.json")
        monkeypatch.setattr(vault, "LEGACY_PATH", tmp_path / "credentials.json")
        return vault

    def test_roundtrip(self, vault_env):
        vault_env.save_vault([{"service": "ssh", "value": "hunter2"}], "pw")
        entries = vault_env.load_vault("pw")
        assert entries[0]["value"] == "hunter2"

    def test_wrong_passphrase_rejected(self, vault_env):
        vault_env.save_vault([{"service": "ssh", "value": "x"}], "correct")
        with pytest.raises(PermissionError):
            vault_env.load_vault("wrong")

    def test_tampered_ciphertext_rejected(self, vault_env):
        vault_env.save_vault([{"service": "ssh", "value": "x"}], "pw")
        blob = json.loads(vault_env.VAULT_PATH.read_text())
        import base64

        blob["ct"] = base64.b64encode(b"tampered").decode()
        vault_env.VAULT_PATH.write_text(json.dumps(blob))
        with pytest.raises(PermissionError):
            vault_env.load_vault("pw")

    def test_init_imports_and_shreds_legacy(self, vault_env):
        vault_env.LEGACY_PATH.write_text(json.dumps({"credentials": [{"service": "web", "value": "secret"}]}))
        msg = vault_env.init_vault("pw")
        assert "1 credential(s)" in msg
        assert not vault_env.LEGACY_PATH.exists()  # plaintext shredded
        assert any(c["value"] == "secret" for c in vault_env.load_vault("pw"))

    def test_add_dedupe_and_list(self, vault_env, capsys):
        vault_env.init_vault("pw", import_legacy=False)
        assert "Stored" in vault_env.add_credential("db", "password", "p@ss", passphrase="pw")
        assert "deduplicated" in vault_env.add_credential(
            "db", "password", "p@ss", passphrase="pw"
        ).lower() or "Already" in vault_env.add_credential("db", "password", "p@ss", passphrase="pw")
        listing = vault_env.list_credentials("pw")
        assert "db" in listing and "p@ss" not in listing  # hidden by default
        assert "p@ss" in vault_env.list_credentials("pw", reveal=True)

    def test_export_redacts(self, vault_env, tmp_path):
        vault_env.init_vault("pw", import_legacy=False)
        vault_env.add_credential("api", "token", "sk-live-123", passphrase="pw")
        out = tmp_path / "exp.json"
        vault_env.export_credentials("pw", out_path=out, redact=True)
        assert "sk-live-123" not in out.read_text()


# ── Dossier ────────────────────────────────────────────────────────────


class TestDossier:
    def _ws(self, tmp_path):
        (tmp_path / "audit_trails").mkdir()
        (tmp_path / "audit_trails" / "e1.json").write_text(
            json.dumps(
                {
                    "engagement": "probe",
                    "started": "2026-08-18T01:00:00+00:00",
                    "ended": "2026-08-18T01:05:00+00:00",
                    "findings": [{"severity": "high"}],
                    "total_actions": 9,
                    "iterations": [{"thought": "scan target.example"}],
                }
            )
        )
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "r.md").write_text("# findings for target.example\n")
        (tmp_path / "failure_db.json").write_text(
            json.dumps([{"target": "target.example", "technique": "sqli", "reason": "WAF 403"}])
        )
        kg = tmp_path / "intel" / "knowledge_graph.json"
        kg.parent.mkdir(parents=True)
        kg.write_text(json.dumps({"target.example": {"blocks": [{"rule": "' OR 1=1"}]}}))
        return kg

    def test_build_and_render(self, tmp_path):
        from suijin.modules.ops.lib.dossier import build_dossier, render_dossier

        kg = self._ws(tmp_path)
        d = build_dossier("target.example", workspace=tmp_path, red_kg=kg)
        assert d["constraints"]["blocks"] == ["' OR 1=1"]
        assert len(d["failures"]) == 1
        assert any("probe" in e for e in d["engagements"])
        assert "reports/r.md" in d["reports"]
        out = render_dossier(d)
        assert "Dossier — target.example" in out
        assert "WAF 403" in out

    def test_unknown_target_is_empty(self, tmp_path):
        from suijin.modules.ops.lib.dossier import build_dossier, render_dossier

        d = build_dossier("nope.example", workspace=tmp_path, red_kg=tmp_path / "intel" / "knowledge_graph.json")
        assert render_dossier(d).count("(none)") >= 2

    def test_agent_tool(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import dossier as dos
        from suijin.modules.tools.lib import dispatch

        kg = self._ws(tmp_path)
        monkeypatch.setattr(dos, "WORKSPACE_DIR", tmp_path)
        monkeypatch.setattr(dos, "RED_KG_PATH", kg)
        out = dispatch.route_tool("target_dossier", {"target": "target.example"}, {})
        assert "Dossier" in out and "WAF 403" in out


# ── Notify ─────────────────────────────────────────────────────────────


class TestNotify:
    def test_file_channel(self, tmp_path):
        from suijin.modules.ops.lib import notify

        log = tmp_path / "notes.log"
        results = notify.send("title", "message body", config={"file": str(log)})
        assert any("appended" in r for r in results)
        assert "message body" in log.read_text()

    def test_no_config(self):
        from suijin.modules.ops.lib import notify

        assert notify.send("t", "m", config={}) == ["notify: no channels configured (suijin/notify.json)"]

    def test_command_channel_substitutes_message(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import notify

        out = tmp_path / "cmd_out.txt"
        cfg = {"command": f"sh -c 'echo {{message}} > {out}'"}
        notify.send("t", "hello world", config=cfg)
        assert out.read_text().strip() == "hello world"


# ── Governance: rules + policy ─────────────────────────────────────────


class TestRules:
    def test_validate_good_and_bad(self, tmp_path):
        from suijin.modules.ops.lib.governance import validate_rules

        good = tmp_path / "rules.json"
        good.write_text(json.dumps([{"name": "ok", "pattern": "/internal", "field": "path", "weight": 4}]))
        assert validate_rules(good) == []
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps([{"name": "x", "pattern": "(unclosed", "field": "nowhere", "weight": 99}]))
        problems = validate_rules(bad)
        assert any("bad regex" in p for p in problems)
        assert any("field" in p for p in problems)
        assert any("weight" in p for p in problems)

    def test_match_rules(self, tmp_path):
        from suijin.modules.ops.lib.governance import match_rules

        rules = [{"name": "webshell", "pattern": "c99shell", "field": "body", "weight": 5, "type": "webshell"}]
        hits = match_rules({"body": "upload c99shell.php"}, rules)
        assert hits == [("webshell", 5)]
        assert match_rules({"body": "normal"}, rules) == []


class TestPolicy:
    def test_no_policy_file_allows_everything(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import governance
        from suijin.modules.ops.lib.governance import check_policy

        monkeypatch.setattr(governance, "POLICY_PATH", tmp_path / "absent.json")
        ok, _ = check_policy("http_request", {"url": "http://203.0.113.9/"})
        assert ok  # opt-in: no file = no enforcement

    def test_policy_file_enforces_scopes(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import governance
        from suijin.modules.ops.lib.governance import check_policy, load_policy

        pol_path = tmp_path / "policy.json"
        pol_path.write_text(json.dumps({"allowed_target_scopes": ["127.0.0.1"]}))
        monkeypatch.setattr(governance, "POLICY_PATH", pol_path)
        pol = load_policy()
        ok, _ = check_policy("http_request", {"url": "http://127.0.0.1:5906/x"}, pol)
        assert ok
        denied, reason = check_policy("http_request", {"url": "http://203.0.113.9/"}, pol)
        assert not denied and "outside allowed scopes" in reason

    def test_scope_exempt_intel_tools(self):
        from suijin.modules.ops.lib.governance import _POLICY_DEFAULT, check_policy

        pol = dict(_POLICY_DEFAULT)
        ok, _ = check_policy("target_dossier", {"target": "anything.example"}, pol)
        assert ok  # intel-only tools are never scope-gated

    def test_private_ranges_covered(self):
        from suijin.modules.ops.lib.governance import _POLICY_DEFAULT, check_policy

        pol = dict(_POLICY_DEFAULT)
        for host in ("10.1.2.3", "192.168.1.10", "172.20.0.5"):
            ok, _ = check_policy("http_request", {"url": f"http://{host}/"}, pol)
            assert ok, host

    def test_blocked_tool_and_arg_pattern(self):
        from suijin.modules.ops.lib.governance import check_policy

        pol = {"allowed_target_scopes": [], "blocked_tools": ["msf_run"], "blocked_arg_patterns": ["rm\\s+-rf"]}
        denied, reason = check_policy("msf_run", {}, pol)
        assert not denied and "blocked" in reason
        denied, reason = check_policy("execute_terminal", {"cmd": "rm -rf /"}, pol)
        assert not denied and "blocked pattern" in reason

    def test_route_tool_enforces_policy(self, monkeypatch, tmp_path):
        from suijin.modules.ops.lib import governance
        from suijin.modules.tools.lib import dispatch

        monkeypatch.setattr(governance, "POLICY_PATH", tmp_path / "policy.json")
        (tmp_path / "policy.json").write_text(
            json.dumps({"allowed_target_scopes": ["127.0.0.1"], "blocked_tools": ["web_search"]})
        )
        out = dispatch.route_tool("web_search", {"query": "x"}, {})
        assert "policy" in out.lower()

    def test_lint_bad_policy(self, tmp_path):
        from suijin.modules.ops.lib.governance import validate_policy

        bad = tmp_path / "policy.json"
        bad.write_text(json.dumps({"blocked_arg_patterns": ["(bad"]}))
        assert any("bad regex" in p for p in validate_policy(bad))


# ── Module SDK ─────────────────────────────────────────────────────────


class TestModuleSdk:
    def test_scaffold_and_validate(self, tmp_path):
        from suijin.modules.tools.lib.module_sdk import scaffold_module, validate_module

        mod = scaffold_module("my_scanner", root=tmp_path)
        assert (mod / "manifest.json").exists()
        ok, problems = validate_module("my_scanner", root=tmp_path)
        assert ok, problems

    def test_validate_catches_missing_tool(self, tmp_path):
        from suijin.modules.tools.lib.module_sdk import scaffold_module, validate_module

        scaffold_module("broken", root=tmp_path)
        mfile = tmp_path / "broken" / "main.py"
        mfile.write_text("def not_the_declared_name():\n    'doc'\n    return ''\n")
        ok, problems = validate_module("broken", root=tmp_path)
        assert not ok and any("not a function" in p for p in problems)

    def test_duplicate_scaffold_rejected(self, tmp_path):
        from suijin.modules.tools.lib.module_sdk import scaffold_module

        scaffold_module("dup", root=tmp_path)
        with pytest.raises(FileExistsError):
            scaffold_module("dup", root=tmp_path)


# ── Provider failover ──────────────────────────────────────────────────


class TestFailover:
    def test_falls_through_on_error(self, monkeypatch):
        from suijin.modules.providers import lib as providers

        calls = []

        def fake_generate(messages, config=None, **kw):
            calls.append(config["provider"])
            return "Error: primary down" if config["provider"] == "zai" else "recovered!"

        monkeypatch.setattr(providers, "generate", fake_generate)
        out = providers.generate_with_failover(
            [{"role": "user", "content": "hi"}], {"provider": "zai", "fallback_providers": ["deepseek"]}
        )
        assert out == "recovered!"
        assert calls == ["zai", "deepseek"]

    def test_success_short_circuits(self, monkeypatch):
        from suijin.modules.providers import lib as providers

        calls = []

        def fake_generate(messages, config=None, **kw):
            calls.append(config["provider"])
            return "fine"

        monkeypatch.setattr(providers, "generate", fake_generate)
        out = providers.generate_with_failover(
            [{"role": "user", "content": "hi"}], {"provider": "zai", "fallback_providers": ["deepseek"]}
        )
        assert out == "fine" and calls == ["zai"]

    def test_all_down_returns_last_error(self, monkeypatch):
        from suijin.modules.providers import lib as providers

        monkeypatch.setattr(providers, "generate", lambda m, c=None, **k: "Error: nope")
        out = providers.generate_with_failover(
            [{"role": "user", "content": "hi"}], {"provider": "zai", "fallback_providers": ["deepseek"]}
        )
        assert out == "Error: nope"


# ── Skill versioning ───────────────────────────────────────────────────


class TestSkillVersioning:
    @pytest.fixture
    def skill_env(self, tmp_path, monkeypatch):
        from suijin.modules.tools.lib import self_improve as si

        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "testskill.py").write_text('TESTSKILL_SKILL_PROMPT = """v1"""\n')
        hist = tmp_path / "skill_history"
        monkeypatch.setattr(si, "SKILLS_DIR", skills)  # v4.1: skills live in the agent module
        monkeypatch.setattr(si, "HISTORY_DIR", hist)
        return si

    def test_edit_snapshots(self, skill_env):
        si = skill_env
        out = si.edit_skill("testskill", "v2 content")
        assert "snapshot" in out
        snaps = si.skill_history("testskill")
        assert len(snaps) == 1
        assert "v1" in snaps[0].read_text()
        assert "v2 content" in (si._skills_dir() / "testskill.py").read_text()

    def test_diff_and_rollback(self, skill_env):
        si = skill_env
        si.edit_skill("testskill", "v2 content")
        si.edit_skill("testskill", "v3 content")
        diff = si.skill_diff("testskill")
        assert "-v2 content" in diff  # latest snapshot vs live
        assert "+v3 content" in diff
        msg = si.skill_rollback("testskill")
        assert "Rolled back" in msg
        live = (si._skills_dir() / "testskill.py").read_text()
        assert "v2 content" in live  # rolled back to previous revision


# ── Housekeeping: campaign / watch / timeline / clean ──────────────────


class TestCampaign:
    def test_probe_and_matrix(self):
        from suijin.modules.ops.lib.housekeeping import render_campaign, run_campaign

        class _Resp:
            def __init__(self, text):
                self.text = text

        class _Session:
            def get(self, url, timeout):
                if url.endswith("/"):
                    return _Resp('routes: "/api/x" "/admin" — FLAG{landing_flag}')
                return _Resp("ok")

        result = run_campaign([{"name": "fakelab", "port": 1}], session=_Session())
        m = result["labs"]["fakelab"]
        assert m["reachable"] and m["flags"] == ["FLAG{landing_flag}"]
        assert "/api/x" in m["hints"]
        assert result["summary"]["reachable"] == 1
        out = render_campaign(result)
        assert "fakelab" in out and "FLAG" not in out  # counts, not raw flags

    def test_unreachable_lab(self):
        from suijin.modules.ops.lib.housekeeping import run_campaign

        class _Dead:
            def get(self, url, timeout):
                raise ConnectionError("down")

        result = run_campaign([{"name": "down", "port": 1}], session=_Dead())
        assert result["labs"]["down"]["reachable"] is False


class TestWatchLines:
    def test_scoring_lines(self):
        from suijin.modules.ops.lib.housekeeping import watch_lines

        lines = [
            json.dumps({"timestamp": "2026-08-18T10:00:00", "method": "GET", "path": "/"}),
            json.dumps(
                {
                    "timestamp": "2026-08-18T10:00:01",
                    "method": "POST",
                    "path": "/login",
                    "body": "{\"u\":\"admin' OR '1'='1\"}",
                }
            ),
            "not-json",
        ]
        out = watch_lines(lines)
        assert len(out) == 2
        assert "INVESTIGATED" in out[1] or "ANOMALOUS" in out[1]
        assert "normal" in out[0]


class TestTimeline:
    def test_merges_artifacts(self, tmp_path):
        from suijin.modules.ops.lib.housekeeping import build_timeline

        (tmp_path / "audit_trails").mkdir()
        (tmp_path / "audit_trails" / "a.json").write_text(
            json.dumps(
                {
                    "engagement": "e",
                    "started": "2026-08-18T01:00:00+00:00",
                    "ended": "2026-08-18T01:05:00+00:00",
                    "findings": [],
                }
            )
        )
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / "s.json").write_text(json.dumps({"saved_at": "20260818_010300", "objective": "obj"}))
        events = build_timeline(workspace=tmp_path)
        kinds = [e["kind"] for e in events]
        assert kinds == ["engagement start", "session saved", "engagement end"]
        assert events[1]["ts"] == "2026-08-18 01:03:00"


class TestClean:
    def _old_file(self, p: Path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("stale log\n")
        old = time.time() - 40 * 86400
        import os

        os.utime(p, (old, old))

    def test_dry_run_lists(self, tmp_path):
        from suijin.modules.ops.lib.housekeeping import clean_workspace

        self._old_file(tmp_path / "outputs" / "job.log")
        out = clean_workspace(apply=False, workspace=tmp_path)
        assert "dry-run" in out and "job.log" in out
        assert (tmp_path / "outputs" / "job.log").exists()

    def test_apply_archives_and_deletes(self, tmp_path):
        import zipfile

        from suijin.modules.ops.lib.housekeeping import clean_workspace

        self._old_file(tmp_path / "outputs" / "sandbox" / "x.txt")
        out = clean_workspace(apply=True, workspace=tmp_path)
        assert "archived" in out
        assert not (tmp_path / "outputs" / "sandbox" / "x.txt").exists()
        zips = list((tmp_path / "exports").glob("cleaned_*.zip"))
        assert zips and "outputs/sandbox/x.txt" in zipfile.ZipFile(zips[0]).namelist()

    def test_fresh_files_kept(self, tmp_path):
        from suijin.modules.ops.lib.housekeeping import clean_workspace

        (tmp_path / "outputs").mkdir()
        (tmp_path / "outputs" / "new.log").write_text("recent")
        out = clean_workspace(apply=True, workspace=tmp_path)
        assert "tidy" in out
        assert (tmp_path / "outputs" / "new.log").exists()


# ── Recon auto-suggest hook ────────────────────────────────────────────


class TestReconHook:
    def test_exploit_leads_appended(self, monkeypatch, tmp_path):
        import suijin.modules.knowledge.lib.kb as kbmod
        import suijin.modules.knowledge.lib.kb_tools as kbt
        from suijin.modules.tools.lib import recon

        # make the KB-present check true WITHOUT a real build (CI has none)
        fake_db = tmp_path / "kb.sqlite3"
        fake_db.write_bytes(b"")
        monkeypatch.setattr(kbmod, "DB_PATH", fake_db)

        def fake_suggest(service, version=""):
            return "Offline suggestions for 'awk'\n[gtfobins] awk is a living-off-the-land binary"

        monkeypatch.setattr(kbt, "suggest_exploit", fake_suggest)
        services = [{"port": 22, "proto": "tcp", "service": "awk", "banner": "awk 5.0"}]
        out = recon._exploit_leads(services)
        assert "Exploit leads" in out
        assert "gtfobins" in out

    def test_no_leads_when_kb_missing(self, monkeypatch):
        import suijin.modules.knowledge.lib.kb as kbmod
        import suijin.modules.tools.lib.recon as recon_mod

        monkeypatch.setattr(kbmod, "DB_PATH", Path("/nonexistent/kb.sqlite3"))
        assert recon_mod._exploit_leads([{"port": 1, "service": "x"}]) == ""
