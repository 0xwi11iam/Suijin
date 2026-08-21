"""REAL battle — actual agent loop, live lab, real detector, ground truth."""

import pytest

from suijin.modules.ops.lib.real_battle import run_real_battle


@pytest.mark.slow
class TestRealBattleMock:
    def test_full_pipeline_end_to_end(self):
        v = run_real_battle(mock=True)
        red, blue = v["red"], v["blue"]
        # the red loop RAN with real tool calls against the live lab
        assert red["tool_calls"] >= 5 and red["requests_issued"] >= 5
        # the flag was captured through a REAL exploit path (X-Admin bypass)
        assert any("admin_bypass" in f for f in red["flags"]), red
        # blue scored the actual traffic with the real detector
        assert blue["traffic_seen"] >= 6 and blue["attack_requests"] >= 5
        assert blue["caught"] >= 2  # SQLi + XSS at minimum
        assert 0.0 <= blue["recall"] <= 1.0
        assert v["mode"] == "mock" and v["winner"] in ("red", "blue", "draw")

    def test_report_persisted(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        v = run_real_battle(mock=True)
        reports = list((tmp_path / "outputs" / "reports").glob("real_battle_*.json"))
        assert reports, "verdict report not written"
        import json

        saved = json.loads(reports[-1].read_text())
        assert saved["red"]["flags"] == v["red"]["flags"]
