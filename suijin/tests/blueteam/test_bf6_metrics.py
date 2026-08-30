"""BF6 — MTTD/MTTR, session report, ATT&CK coverage."""

import io

import pytest

import suijin.modules.platform.lib.workspace as ws
from suijin.modules.blueteam.lib.blue.cases import CaseStore
from suijin.modules.blueteam.lib.blue.metrics import (
    case_metrics,
    render_session_report,
    save_report,
    session_metrics,
)


@pytest.fixture(autouse=True)
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
    ws._CURRENT_ENGAGEMENT = None
    yield tmp_path
    ws._CURRENT_ENGAGEMENT = None


class TestCaseMetrics:
    def _case(self, mttd=True, mttr=True):
        c = {
            "first_event_at": "2026-01-01T00:00:00Z",
            "detected_at": "2026-01-01T00:00:05Z" if mttd else None,
            "contained_at": "2026-01-01T00:00:12Z" if mttr else None,
            "closed_at": "2026-01-01T00:01:00Z",
            "status": "closed",
            "severity": 7,
        }
        return c

    def test_mttd_mttr_computed(self):
        m = case_metrics(self._case())
        assert m["mttd_s"] == 5.0  # 5 seconds to detect
        assert m["mttr_s"] == 7.0  # 7 seconds to contain
        assert m["dwell_s"] == 60.0  # total dwell

    def test_missing_timestamps(self):
        m = case_metrics(self._case(mttd=False, mttr=False))
        assert m["mttd_s"] is None
        assert m["mttr_s"] is None


class TestSessionMetrics:
    def test_full_metrics(self):
        s = CaseStore("m")
        s.record_event("1.1.1.1", "sql_injection", 7, "/login")
        c = s.record_event("2.2.2.2", "xss_attempt", 5, "/search")
        s.record_action(c["id"], "BLOCK", "canary")

        m = session_metrics(s, {"total": 100, "detected": 5, "blocked": 3})
        assert m["cases"] == 2
        assert m["actors"] == 2
        assert m["attack"]["detected"] != []
        assert m["attack"]["coverage_pct"] > 0
        assert m["by_type"]["sql_injection"] == 1

    def test_empty_metrics(self):
        s = CaseStore("m")
        m = session_metrics(s)
        assert m["cases"] == 0
        assert m["mttd_avg_s"] is None
        assert m["attack"]["detected"] == []


class TestSessionReport:
    def test_render_report(self):
        s = CaseStore("m")
        s.record_event("1.1.1.1", "sql_injection", 8, "/api")
        m = session_metrics(s, {"total": 50, "detected": 2, "blocked": 1, "tarpitted": 1, "deceived": 0})
        md = render_session_report(m, "hill_ctf")
        assert "Blue Session Report" in md
        assert "50" in md and "2" in md
        assert "ATT&CK" in md and "T1190" in md

    def test_save_report(self):
        s = CaseStore("m")
        m = session_metrics(s, {"total": 10})
        path = save_report(m, "test")
        assert path.exists()
        assert path.suffix == ".md"
        assert "Blue Session Report" in path.read_text()

    def test_report_command_includes_metrics(self):
        from rich.console import Console

        from suijin.modules.blueteam.lib.blue.console_ui import BlueConsoleUI
        from suijin.modules.blueteam.lib.blue.session_runner import BlueCommandBox

        c = Console(file=io.StringIO(), record=True, width=100, force_terminal=True)
        ui = BlueConsoleUI(c, target="test")
        ui.start()
        box = BlueCommandBox(ui, c)
        box.dispatch("/report")
        out = c.export_text()
        assert "ATT&CK" in out and "cases" in out
        ui.stop()
