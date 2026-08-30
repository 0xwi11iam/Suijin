"""BF4 — cases, dossiers, and the organ wiring.

CaseStore: lifecycle (new->contained->monitoring->closed), reopen on
same-actor, timeline, MTTD/MTTR timestamps. Actor dossiers merge cases
+ KG + enforcement. /dossier and /case commands in the console box.
"""

import pytest

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.blueteam.lib.blue.cases import ATTACK_MAP, CaseStore


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    ws.set_engagement("bf4 test")
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


class TestCaseStore:
    def test_case_opens_on_attack(self):
        s = CaseStore("bf4 test")
        c = s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        assert c["id"] == "CASE-0001"
        assert c["status"] == "new"
        assert c["actor_ip"] == "1.2.3.4"
        assert c["severity"] == 7
        assert len(c["timeline"]) == 1

    def test_same_actor_attaches_to_same_case(self):
        s = CaseStore("bf4 test")
        s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        s.record_event("1.2.3.4", "sql_injection", 8, "/api")
        s.record_event("1.2.3.4", "xss_attempt", 5, "/search")
        cases = s.list_cases()
        assert len(cases) == 1  # all events on ONE case (dedup)
        assert cases[0]["severity"] == 8  # escalated
        assert len(cases[0]["timeline"]) == 3

    def test_different_actors_separate_cases(self):
        s = CaseStore("bf4 test")
        s.record_event("1.1.1.1", "sql_injection", 7, "/a")
        s.record_event("2.2.2.2", "xss_attempt", 5, "/b")
        assert len(s.list_cases()) == 2

    def test_action_transitions_to_contained(self):
        s = CaseStore("bf4 test")
        c = s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        assert c["status"] == "new"
        s.record_action(c["id"], "BLOCK", "canary trip")
        detail = s.case_detail(c["id"])
        assert detail["status"] == "contained"
        assert detail["contained_at"] is not None  # MTTR timestamp captured

    def test_lifecycle_transitions(self):
        s = CaseStore("bf4 test")
        c = s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        s.record_action(c["id"], "TARPIT", "auto")
        s.transition(c["id"], "monitoring")
        assert s.case_detail(c["id"])["status"] == "monitoring"
        s.transition(c["id"], "closed", "attacker blocked")
        assert s.case_detail(c["id"])["status"] == "closed"

    def test_reopen_on_new_activity(self):
        s = CaseStore("bf4 test")
        c = s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        s.transition(c["id"], "closed", "resolved")
        # new activity from same actor opens a NEW case (old stays closed)
        c2 = s.record_event("1.2.3.4", "ssrf_attempt", 8, "/webhook")
        assert c2["id"] != c["id"]
        assert s.case_detail(c["id"])["status"] == "closed"
        assert s.case_detail(c2["id"])["status"] == "new"

    def test_attck_mapping(self):
        s = CaseStore("bf4 test")
        c = s.record_event("1.2.3.4", "sql_injection", 7, "/x")
        assert c["mitre"] == "T1190"
        assert ATTACK_MAP["sql_injection"] == "T1190"
        assert len(ATTACK_MAP) >= 15

    def test_persistence(self):
        s = CaseStore("bf4 test")
        s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        # fresh store reads the same index
        s2 = CaseStore("bf4 test")
        assert len(s2.list_cases()) == 1

    def test_stats(self):
        s = CaseStore("bf4 test")
        s.record_event("1.1.1.1", "sql_injection", 7, "/a")
        s.record_event("2.2.2.2", "xss_attempt", 5, "/b")
        stats = s.get_stats()
        assert stats["total"] == 2
        assert stats["actors"] == 2
        assert stats["open"] == 2

    def test_actor_summary(self):
        s = CaseStore("bf4 test")
        s.record_event("9.9.9.9", "sql_injection", 7, "/a")
        s.record_event("9.9.9.9", "xss_attempt", 6, "/b")
        summ = s.actor_summary("9.9.9.9")
        assert summ["total_cases"] == 1
        assert summ["max_severity"] == 7
        assert "sql_injection" in summ["attack_types"]
        assert "T1190" in summ["mitre_techniques"]

    def test_watcher_finding_attaches(self):
        s = CaseStore("bf4 test")
        c = s.record_event("1.2.3.4", "sql_injection", 7, "/login")
        s.record_watcher(c["id"], "/api", "auth_fail_velocity", 4)
        detail = s.case_detail(c["id"])
        assert any(e["kind"] == "watcher" for e in detail["timeline"])


class TestActorDossier:
    def test_build_and_render(self):
        from suijin.modules.blueteam.lib.blue.dossier import build_dossier, render_dossier

        s = CaseStore("bf4 test")
        s.record_event("5.5.5.5", "sql_injection", 8, "/api/login")

        class MockKG:
            def get_attacker_history(self, ip):
                return {
                    "total_flags": 3,
                    "attacks": [{"type": "sqli"}],
                    "defenses": [],
                    "attacker": {"first_seen": "2026-01-01"},
                }

        d = build_dossier("5.5.5.5", s, MockKG(), {"blocks": {"5.5.5.5": {}}, "canary_hits": [], "redirects": {}})
        assert d["cases"]["total_cases"] == 1
        assert d["kg"]["total_flags"] == 3
        assert d["enforcement"]["blocked"] is True
        assert d["richness"] > 0

        md = render_dossier(d)
        assert "5.5.5.5" in md and "sql_injection" in md and "T1190" in md and "BLOCKED" in md


class TestConsoleCommands:
    def test_dossier_command(self):
        import io

        from rich.console import Console

        from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        c = Console(file=io.StringIO(), record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(c, target="test")
        ui.start()
        box = BlueCommandBox(ui, c)
        box.dispatch("/dossier 5.5.5.5")
        out = c.export_text()
        assert "Dossier" in out or "5.5.5.5" in out
        ui.stop()

    def test_case_command(self):
        import io

        from rich.console import Console

        from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        c = Console(file=io.StringIO(), record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(c, target="test")
        ui.start()
        box = BlueCommandBox(ui, c)
        box.dispatch("/case")
        out = c.export_text()
        assert "no cases" in out or "cases" in out
        ui.stop()

    def test_report_includes_cases(self):
        import io

        from rich.console import Console

        from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        c = Console(file=io.StringIO(), record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(c, target="test")
        ui.start()
        box = BlueCommandBox(ui, c)
        box.dispatch("/report")
        out = c.export_text()
        assert "cases" in out
        ui.stop()
