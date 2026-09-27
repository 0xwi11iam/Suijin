"""The engagement-review round — every fix traced to a live audit trail.

Findings came from reviewing a real run's trail (referred to generically
per AGENTS.md): trail reasoning always empty, findings never landing in
the trail, dossier reading sources that do not exist, the ask panel
showing the literal word "Answer" instead of the question, the strip
wrapping into ghost rows, drift flagging intel consults, fireteams
spawned without doctrine, catalogs split by agent-invented engagement
names, and bypass_403 counting 404s as bypass wins.

All hosts here are example.com.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def trail(tmp_path, monkeypatch):
    import suijin.modules.tools.lib.audit_trail as at

    monkeypatch.setattr(at, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(at, "_last_flush", 0.0)
    monkeypatch.setattr(at, "_dirty", False)
    at.start_audit("example-engagement")
    yield at, tmp_path
    at._current_trail = None


class TestTrailTruth:
    def test_record_finding_lands_in_the_audit_trail(self, trail, monkeypatch):
        """A successful record_finding used to leave the trail's findings
        list EMPTY — every downstream reader (dossier, reviews) understated
        every productive run."""
        import suijin.modules.tools.lib.intel as intel

        at, tmp_path = trail
        kg = type("K", (), {"add_constraint": lambda *a, **k: None, "get_constraints": staticmethod(lambda t: [])})
        monkeypatch.setattr("suijin.modules.loader.load_local_module", lambda name: kg, raising=False)
        intel.record_finding("example.com", "behavior", "headline finding", "evidence text")
        at.end_audit(0.0)
        data = json.loads((tmp_path / "example-engagement.json").read_text())
        assert data["findings"], "record_finding did not reach the audit trail"
        assert data["findings"][0]["endpoint"] == "example.com"
        assert "headline finding" in data["findings"][0]["description"]

    def test_streamed_reasoning_is_stashed_and_capped(self, monkeypatch):
        """Reasoning models put deliberation in the stream, never in the
        decision JSON — the provider layer now exposes it so the trail can
        persist it."""
        from suijin.modules.providers import lib as prov

        prov._stash_reasoning("deliberation " * 9000)
        assert prov.get_usage()["last_reasoning"].startswith("deliberation")
        assert len(prov.get_usage()["last_reasoning"]) == prov._LAST_REASONING_CAP
        prov._stash_reasoning("")
        assert prov.get_usage()["last_reasoning"] == ""


class TestDriftFormattingAndExemptions:
    def test_format_cause_is_readable_not_a_dict(self):
        from suijin.modules.redteam.lib.intel.drift_analyser import format_cause

        line = format_cause(
            {
                "action_index": 3,
                "action": "http_request: Inspect the live robots policy at low volume to identify permitted paths",
                "patterns": ["low_goal_overlap"],
            }
        )
        assert line.startswith("#3 http_request — low goal overlap: Inspect the live robots policy")
        assert "{" not in line and "action_index" not in line
        long = format_cause({"action_index": 9, "action": "nmap_scan: " + "word " * 30, "patterns": ["hallucination"]})
        assert long.endswith("…") and "\\n" not in long  # truncated at a word boundary, never mid-word

    def test_intel_consults_are_not_drift(self):
        """Consulting the dossier/KG shares no keywords with objective prose
        and was flagged as drift — punishing exactly the mandated pre-test
        diligence."""
        from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift

        actions = [
            "http_request: fetch the homepage of example.com",
            "target_dossier: review everything known about this target before testing",
            "check_knowledge: prior constraints and verified history",
            "memory_recall: what worked before on this host",
            "search_kb: technique guidance for the observed stack",
        ]
        result = analyse_drift("example.com", actions)
        causes = [c for c in result["drift_causes"] if "low_goal_overlap" in c["patterns"]]
        assert not causes, f"intel consults flagged as drift: {causes}"

    def test_real_divergence_is_still_caught(self):
        from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift

        actions = [
            "http_request: fetch homepage",
            "nmap_scan: sweep unrelated subnet",
            "execute_terminal: download unrelated tooling",
            "sqlmap: attack a host neither in nor like the objective",
        ]
        result = analyse_drift("example.com only, nothing else", actions)
        assert result["drift_detected"], "genuine divergence must still fire"

    def test_tui_drift_note_renders_structured_lines(self):
        from rich.console import Console

        from suijin.client.tui.console_ui import EngagementUI

        ui = EngagementUI(Console(record=True, width=100, force_terminal=True), objective="t")
        ui.drift(
            {
                "drift_causes": [
                    {"action_index": 3, "action": "nmap_scan: sweep unrelated subnet", "patterns": ["low_goal_overlap"]}
                ],
                "suggestions": ["Agent actions diverged from goal — reinforce objective."],
            }
        )
        text = ui.console.export_text()
        assert "low goal overlap" in text
        assert "{" not in text.split("Drift")[1][:200], "raw dict repr leaked into the note"

    def test_pause_banner_announces_pause_once(self):
        """The strip's PAUSED badge owns the state; the banner must not
        stack a second 'Paused' on top of it."""
        from suijin.client.tui import session_control

        body = session_control.PAUSE_BANNER.lower()
        assert "paused" not in body, "banner duplicates the strip's PAUSED badge"
        assert "resume" in body, "the banner still carries the how-to"


class TestDossierRealSources:
    @pytest.fixture()
    def ws(self, tmp_path, monkeypatch):
        import suijin.modules.ops.lib.dossier as dos

        ws = tmp_path / "ws"
        eng = ws / "engagements" / "20260101_000000_example.com_run"
        (eng / "audit_trails").mkdir(parents=True)
        (eng / "audit_trails" / "example.com.json").write_text(
            json.dumps(
                {
                    "engagement": "example.com run",
                    "findings": [{"x": 1}],
                    "total_actions": 7,
                    "iterations": [
                        {"action": {"tool": "sqlmap", "args": {"url": "https://example.com/x"}, "success": False}}
                    ],
                }
            )
        )
        (ws / "engagements" / "20260101_000000_other").mkdir(parents=True)
        monkeypatch.setattr(dos, "WORKSPACE_DIR", ws)
        return ws

    def test_engagement_history_reads_real_trails(self, ws):
        import suijin.modules.ops.lib.dossier as dos

        d = dos.build_dossier("example.com", workspace=ws)
        assert any("example.com run" in e for e in d["engagements"]), d["engagements"]
        assert any("1 findings" in e for e in d["engagements"])

    def test_failures_mined_from_trails_not_dead_db(self, ws):
        import suijin.modules.ops.lib.dossier as dos

        d = dos.build_dossier("example.com", workspace=ws)
        assert any("sqlmap" in f for f in d["failures"]), d["failures"]

    def test_what_worked_section_present(self, ws):
        import suijin.modules.ops.lib.dossier as dos

        d = dos.build_dossier("example.com", workspace=ws)
        assert "worked" in d  # the section exists (values may be empty on a clean ws)

    def test_attack_memory_reads_engagement_catalogs(self, tmp_path, monkeypatch):
        """what_worked only globbed the legacy root — blind since the
        restructure moved catalogs into engagements/."""
        from suijin.modules.agent.lib import attack_memory as am

        cat = tmp_path / "engagements" / "20260101_000000_example.com_run" / "exploits" / "example-com"
        cat.mkdir(parents=True)
        (cat / "catalog.json").write_text(
            json.dumps(
                {
                    "entries": {
                        "EXP-001": {
                            "status": "CONFIRMED",
                            "vuln_class": "information_disclosure",
                            "title": "headers leak build info",
                            "target": "https://example.com",
                        }
                    }
                }
            )
        )
        monkeypatch.setattr(am, "_catalog_roots", lambda: [tmp_path / "engagements"])
        lines = am.what_worked("example.com")
        assert any("information_disclosure" in str(x) for x in lines), lines


class TestCatalogEngagementSlug:
    def test_elaborated_names_resolve_to_the_live_engagement(self, tmp_path, monkeypatch):
        """The agent invents engagement names mid-run ('x' then 'x detailed
        validation') — anything containing the live slug is the SAME
        engagement; only a different target earns a subdir."""
        import suijin.modules.tools.lib.exploit_catalog as ec

        live = tmp_path / "engagements" / "20260101_000000_example.com" / "exploits"
        live.mkdir(parents=True)
        monkeypatch.setattr("suijin.modules.platform.lib.workspace.exploits_dir", lambda: live)
        assert ec._engagement_dir("example.com") == live
        assert ec._engagement_dir("example.com detailed validation") == live, "split the catalog"
        assert ec._engagement_dir("other-target.net") == live / "other-target.net"


class TestFireteamDoctrine:
    def test_system_prompt_carries_the_doctrine(self):
        from suijin.modules.agent.lib.nodes.subagent_node import _build_system_prompt

        prompt = _build_system_prompt("probe the login flow", 8, None, doctrine="SCOPE — stay within example.com")
        assert "DOCTRINE" in prompt and "stay within example.com" in prompt
        assert "SCOPE — stay within" in prompt

    def test_prompt_without_doctrine_has_no_empty_section(self):
        from suijin.modules.agent.lib.nodes.subagent_node import _build_system_prompt

        prompt = _build_system_prompt("probe", 8, None, doctrine="")
        assert "DOCTRINE" not in prompt

    def test_run_subagent_forwards_doctrine(self):
        import inspect

        from suijin.modules.agent.lib.nodes.subagent_node import deploy_fireteam, run_subagent

        assert "route_tool_fn, doctrine)" in inspect.getsource(run_subagent)  # into the prompt
        assert "doctrine=doctrine" in inspect.getsource(deploy_fireteam)  # through the spawn


class TestBypass403Verdicts:
    def test_non_403_baseline_short_circuits(self, monkeypatch):
        """On a 200/404 baseline every status difference counted as a 'win'
        — a live probe reported 404s and 501s as bypasses."""
        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        monkeypatch.setattr(ht, "http_request", lambda m, u, headers=None, body="": "Status: 200\nBody:\nok")
        out = bypass_403("https://example.com/admin")
        assert "nothing to bypass" in out and "VERDICT" not in out

    def test_error_status_changes_are_not_wins(self, monkeypatch):
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        def fake_404_variants(method, url, headers=None, body=""):
            if url == "https://example.com/admin" and not headers:
                return "Status: 403\nBody:\nforbidden"  # the baseline
            return "Status: 404\nBody:\nnope"  # every variant 404s

        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")

        monkeypatch.setattr(ht, "http_request", fake_404_variants)
        out = bypass_403("https://example.com/admin")
        assert "0 bypasses" in out and "404" in out and "not a bypass" not in out

    def test_2xx_is_a_real_bypass(self, monkeypatch):
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        def fake(method, url, headers=None, body=""):
            # baseline 403; the X-Original-URL carrier gets 200
            if headers and "X-Original-URL" in headers:
                return "Status: 200\nBody:\nadmin dashboard"
            return "Status: 403\nBody:\nforbidden"

        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")

        monkeypatch.setattr(ht, "http_request", fake)
        out = bypass_403("https://example.com/admin")
        assert "BYPASSED" in out and "X-Original-URL" in out

    def test_rate_limited_rows_are_inconclusive_not_enforced(self, monkeypatch):
        from suijin.modules.tools.lib.bypass_403 import bypass_403

        def fake_throttled_variants(method, url, headers=None, body=""):
            if url == "https://example.com/admin" and not headers:
                return "Status: 403\nBody:\nforbidden"  # baseline lands
            return "RATE LIMITED: target is throttling"  # variants go dark

        import importlib

        ht = importlib.import_module("suijin.modules.tools.lib.http_tools")

        monkeypatch.setattr(ht, "http_request", fake_throttled_variants)
        out = bypass_403("https://example.com/admin")
        assert "inconclusive" in out and "do NOT record" in out
