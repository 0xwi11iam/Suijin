"""BF7 — FP allowlist, rule drafting, playbook outcomes."""

import io

import pytest
from rich.console import Console

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.blueteam.lib.blue.cases import CaseStore
from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI
from suijin.modules.blueteam.lib.blue.learning import (
    draft_rule,
    fp_allowlist_add,
    fp_allowlist_check,
    list_drafts,
    playbook_effectiveness,
    promote_draft,
    record_playbook_outcome,
)
from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


class TestFPAllowlist:
    def test_add_and_check_signal(self):
        fp_allowlist_add("scanner_ua", "internal monitoring")
        assert fp_allowlist_check("scanner_ua", path="/health")
        assert not fp_allowlist_check("sql_injection", path="/login")

    def test_check_path_regex(self):
        fp_allowlist_add(r"/health", "health endpoint")
        assert fp_allowlist_check("anything", path="/health")
        assert not fp_allowlist_check("anything", path="/admin")

    def test_empty_allowlist(self):
        assert not fp_allowlist_check("scanner_ua")

    def test_fp_command(self):
        c = Console(file=io.StringIO(), record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(c, target="t")
        ui.start()
        BlueCommandBox(ui, c).dispatch("/fp scanner_ua")
        assert "allowlisted" in c.export_text()
        ui.stop()


class TestRuleDrafting:
    def test_draft_and_list(self):
        d = draft_rule(r"sitemap_\d+\.xml", 3, "sitemap enumeration pattern missed by detector")
        assert d["id"] == "DRAFT-001"
        assert d["status"] == "draft"
        drafts = list_drafts()
        assert len(drafts) == 1

    def test_promote(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "suijin.modules.blueteam.lib.blue.learning._detector_rules_path",
            lambda: tmp_path / "detector_rules.json",
        )
        draft_rule(r"\.git/config", 8, "git directory exposure")
        result = promote_draft("DRAFT-001")
        assert "promoted" in result
        rules = __import__("json").loads((tmp_path / "detector_rules.json").read_text())
        assert len(rules) == 1
        assert rules[0]["weight"] == 8


class TestPlaybookOutcomes:
    def test_record_and_aggregate(self):
        s = CaseStore("pb")
        c = s.record_event("1.1.1.1", "brute_force", 7, "/login")
        record_playbook_outcome(c["id"], "brute_force", "contained after 3 attempts blocked", s)
        record_playbook_outcome(c["id"], "sqli_response", "missed — payload bypassed detector", s)
        stats = playbook_effectiveness(s)
        assert stats["brute_force"]["effective"] == 1
        assert stats["sqli_response"]["ineffective"] == 1
        assert stats["brute_force"]["invoked"] == 1
