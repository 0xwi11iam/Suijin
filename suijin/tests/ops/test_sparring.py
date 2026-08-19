"""Sparring mode — detector practice with regression scoring.

Contract: a spar fires a fixed synthetic volley (attacks the detector
MUST catch + benign traffic it must NOT flag) through the REAL blue
detector, scores it, and compares against a stored baseline:
regression / improvement / stable. Baselines live under
outputs/spar_baselines/ (an artifact dir). --fail-on-regression gives
CI semantics.
"""

from suijin.modules.ops.lib.sparring import _score_volley, render_spar, run_spar


class TestVolley:
    def test_detector_scores_are_real(self):
        """The volley must go through the actual anomaly detector."""
        r = _score_volley()
        assert r["attacks"] + r["benign"] >= 10
        assert 0.0 <= r["f1"] <= 1.0
        assert r["caught"] + r["missed"] == r["attacks"]
        assert r["false_alarms"] + (r["benign"] - r["false_alarms"]) >= 0


class TestSparCycle:
    def test_baseline_then_stable_then_regression(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)

        # first run saves the baseline automatically
        r1, line1 = run_spar(name="unit")
        assert r1["verdict"] == "baseline-saved"
        assert (tmp_path / "outputs" / "spar_baselines" / "unit.json").exists()

        # identical second run = stable
        r2, line2 = run_spar(name="unit")
        assert r2["verdict"] == "stable"
        assert "STABLE" in line2

        # tamper the baseline upward -> regression + fail flag
        bp = tmp_path / "outputs" / "spar_baselines" / "unit.json"
        import json

        data = json.loads(bp.read_text())
        data["f1"] = min(1.0, data["f1"] + 0.2)
        bp.write_text(json.dumps(data))
        r3, line3 = run_spar(name="unit", fail_on_regression=True)
        assert r3["verdict"] == "regression"
        assert r3.get("fail") is True
        assert "REGRESSION" in line3

    def test_render_mentions_paths(self, tmp_path, monkeypatch):
        from suijin.modules.platform.lib import workspace as ws

        monkeypatch.setattr(ws, "WORKSPACE_DIR", tmp_path)
        run_spar(name="render")
        r, line = run_spar(name="render")
        text = render_spar(r, line)
        assert "F1" in text and "attacks" in text and "false alarms" in text
