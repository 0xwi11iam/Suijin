"""Tests for the blue detection replay harness + battle scoring logic."""

import json

import pytest

from medusa.core.blue.traffic import replay_harness as rh
from medusa.tools import battle as bt


def _entry(method="GET", path="/", body="", ua="curl/8", ip="127.0.0.1"):
    return {"method": method, "path": path, "query": "", "body": body, "ip": ip, "user_agent": ua, "headers": {}}


class TestLabeling:
    def test_sqli_labeled_attack(self):
        label, atk = rh.label_heuristic(_entry("POST", "/auth/login", body="{\"u\":\"admin' OR '1'='1\"}"))
        assert label == "attack" and atk == "sqli"

    def test_scanner_ua_labeled_attack(self):
        label, atk = rh.label_heuristic(_entry(ua="sqlmap/1.7.10#stable"))
        assert label == "attack" and atk == "scanner_ua"

    def test_clean_get_labeled_benign(self):
        label, atk = rh.label_heuristic(_entry(path="/api/users"))
        assert label == "benign" and atk == ""

    def test_weird_path_is_unknown_not_benign(self):
        label, _ = rh.label_heuristic(_entry(path="/some/deeply/nested/odd/path"))
        assert label == "unknown"

    def test_labels_file_overrides(self, tmp_path):
        lf = tmp_path / "labels.jsonl"
        lf.write_text(json.dumps({"label": "attack", "any": ["/secret-path"], "type": "custom"}) + "\n")
        out = rh.label_entries([_entry(path="/secret-path")], lf)
        assert out[0] == ("attack", "custom")


class TestMetrics:
    LABELS = [("attack", "sqli"), ("attack", "xss"), ("benign", ""), ("benign", ""), ("unknown", "")]
    SCORES = [
        {"score": 6, "signals": ["sql_injection"]},
        {"score": 3, "signals": []},  # missed at t=5, caught at t=3
        {"score": 1, "signals": []},
        {"score": 6, "signals": []},  # false positive at any t<=6
        {"score": 9, "signals": []},  # unknown label — always skipped
    ]

    def test_confusion_counts(self):
        m = rh.metrics(self.LABELS, self.SCORES, threshold=5)
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (1, 1, 1, 1)
        assert m["skipped_unknown"] == 1
        assert m["precision"] == pytest.approx(0.5)
        assert m["recall"] == pytest.approx(0.5)
        assert m["f1"] == pytest.approx(0.5)

    def test_lower_threshold_catches_more(self):
        m = rh.metrics(self.LABELS, self.SCORES, threshold=3)
        assert (m["tp"], m["fp"], m["tn"], m["fn"]) == (2, 1, 1, 0)
        assert m["recall"] == pytest.approx(1.0)
        assert m["precision"] == pytest.approx(2 / 3)

    def test_sweep_and_best(self):
        rows = rh.sweep(self.LABELS, self.SCORES, lo=2, hi=9)
        assert [r["threshold"] for r in rows] == list(range(2, 10))
        best = rh.best_threshold(rows)
        assert best["f1"] == max(r["f1"] for r in rows)


class TestReplayScores:
    def test_real_scorer_runs(self):
        entries = [_entry(path="/") for _ in range(5)] + [
            _entry("POST", "/auth/login", body="{\"u\":\"admin' OR '1'='1\"}")
        ]
        scores = rh.replay_scores(entries, baseline=5)
        assert scores[0]["score"] >= 1
        assert scores[-1]["score"] >= 5  # SQLi must trip the detector

    def test_render_eval(self):
        entries = (
            [_entry(path="/")] * 5 + [_entry("POST", "/auth/login", body='"role":"admin"')] + [_entry(ua="nikto/2.1")]
        )
        labels = rh.label_entries(entries)
        scores = rh.replay_scores(entries)
        out = rh.render_eval(labels, scores, default_threshold=5)
        assert "traffic entries: 7" in out
        assert "@ threshold 5" in out
        assert "best F1" in out


class TestBattleLogic:
    """Offline pieces of battle mode (watchdog scoring + state math)."""

    def test_red_blue_score_math(self):
        s = bt.BattleState()
        s.red_flags = ["FLAG{a}", "FLAG{b}"]
        s.red_classes_hit = ["sqli", "sqli", "recon"]
        assert s.red_score() == 200 + 50  # 2 flags + 2 unique classes
        s.blue_detected = 4
        s.blue_tarpitted = 2
        s.blue_blocked = True
        assert s.blue_score() == 40 + 50 + 50

    def test_watchdog_detects_and_tarpits(self, tmp_path):
        log = tmp_path / "traffic.jsonl"
        tarpit = tmp_path / "tarpit.json"
        s = bt.BattleState()
        w = bt.BlueWatchdog(s, log, tarpit)
        log.write_text(json.dumps(_entry("POST", "/auth/login", body="{\"u\":\"admin' OR '1'='1\"}")) + "\n")
        w.poll()
        assert s.blue_detected == 1
        assert s.blue_tarpitted == 1
        assert tarpit.exists()
        assert "127.0.0.1" in tarpit.read_text()
        assert not w.is_blocked("127.0.0.1")

    def test_watchdog_blocks_critical(self, tmp_path):
        log = tmp_path / "traffic.jsonl"
        s = bt.BattleState()
        w = bt.BlueWatchdog(s, log, tmp_path / "t.json")
        # stacked signals: sqli + xss + traversal + scanner UA -> critical
        e = _entry("POST", "/x", body="' OR '1'='1 <script>../../etc/passwd")
        e["user_agent"] = "sqlmap/1.7"
        log.write_text(json.dumps(e) + "\n")
        w.poll()
        assert s.blue_detected == 1
        assert s.blue_blocked is True
        assert w.is_blocked("127.0.0.1")

    def test_battle_report(self):
        s = bt.BattleState()
        s.red_flags = ["FLAG{x}"]
        s.blue_detected = 2
        s.events.append("red: captured FLAG{x}")
        rep = bt.battle_report(s, 42.0)
        assert "Battle Report" in rep
        assert "Winner" in rep
        assert "red: captured FLAG{x}" in rep
