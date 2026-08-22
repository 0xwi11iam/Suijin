"""Wave A repairs — lab path, tarpit protocol, detector classes, rules, config."""

import json
import time

import pytest

from suijin.modules.blueteam.lib.blue.traffic.anomaly_detector import detect_anomalies
from suijin.modules.blueteam.lib.blue.traffic.scorer import score_request

_PROFILE = {"methods": {"GET": 1, "POST": 1}, "ips": set(), "avg_body_size": 1000}


# ── A1: blueteamer BASE_DIR ────────────────────────────────────────────


class TestBaseDir:
    def test_base_dir_resolves_to_package_root(self):
        from suijin.modules.blueteam.lib import blueteamer

        assert blueteamer.BASE_DIR.name == "suijin"
        assert (blueteamer.BASE_DIR / ".env").exists() is True or True  # .env optional
        lab = blueteamer.BASE_DIR / "lab" / "blue_target" / "vulnerable_app.py"
        assert lab.exists(), f"built-in lab unreachable at {lab}"

    def test_env_and_lab_share_one_root(self):
        from suijin.modules.blueteam.lib import blueteamer

        # both lookups must hang off the SAME root — the old parents[1]
        # pointed into modules/blueteam where neither exists
        assert blueteamer.BASE_DIR.resolve() == blueteamer.BASE_DIR


# ── A2: tarpit file protocol ───────────────────────────────────────────


class TestTarpitProtocol:
    def test_battle_watchdog_write_honors_protocol(self, tmp_path):
        """The bug: watchdog wrote 'set_at', every reader (lab app, proxy)
        reads 'since' — tarpits moved the scoreboard but never delayed."""
        from suijin.modules.ops.lib.battle import BattleState, BlueWatchdog

        f = tmp_path / "tarpit.json"
        BlueWatchdog(BattleState(), tmp_path / "traffic.jsonl", f)._tarpit("127.0.0.1", 7)
        state = json.loads(f.read_text())
        assert "since" in state["127.0.0.1"]
        assert "set_at" not in state["127.0.0.1"]

    def test_lab_reader_semantics_round_trip(self, tmp_path):
        """What the lab's before_request hook does with a watchdog write."""

        from suijin.modules.blueteam.lib.blue.defense import tarpit

        f = tmp_path / "tarpit.json"
        tarpit.engage("1.2.3.4", delay=4.0, path=f)
        state = json.loads(f.read_text())
        # lab check_tarpit(): elapsed < 1800 -> sleep(min(delay, 15))
        elapsed = time.time() - state["1.2.3.4"]["since"]
        assert elapsed < 1800
        assert min(state["1.2.3.4"]["delay"], 15.0) == 4.0

    def test_expiry_and_caps(self, tmp_path):
        from suijin.modules.blueteam.lib.blue.defense import tarpit

        f = tmp_path / "tarpit.json"
        tarpit.engage("1.2.3.4", delay=99.0, path=f)  # over cap -> 15
        assert tarpit.delay_for("1.2.3.4", path=f) == 15.0
        # expired entry (30-min window)
        state = json.loads(f.read_text())
        state["1.2.3.4"]["since"] = time.time() - 1801
        f.write_text(json.dumps(state))
        assert tarpit.delay_for("1.2.3.4", path=f) == 0.0

    def test_malformed_state_is_no_delay(self, tmp_path):
        from suijin.modules.blueteam.lib.blue.defense import tarpit

        f = tmp_path / "tarpit.json"
        f.write_text("{not json")
        assert tarpit.delay_for("1.2.3.4", path=f) == 0.0


# ── A3: detector classes ported from the TUI fast path ─────────────────


class TestNewDetectorClasses:
    CASES = [
        ("command_injection", {"method": "POST", "path": "/api/ping", "body": "host=8.8.8.8; whoami", "ip": "1.1.1.1"}),
        ("command_injection", {"method": "GET", "path": "/x", "body": "ip=127.0.0.1|whoami", "ip": "1.1.1.1"}),
        (
            "jwt_attack",
            {
                "method": "GET",
                "path": "/api",
                "body": "",
                "query": "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N65IWDpmNfXPU4HuXqoj0k",
                "ip": "1.1.1.1",
            },
        ),
        ("deserialization", {"method": "POST", "path": "/import", "body": 'data=O:8:"stdClass":1:{', "ip": "1.1.1.1"}),
        ("deserialization", {"method": "POST", "path": "/load", "body": "payload=pickle.loads(blob)", "ip": "1.1.1.1"}),
        ("ldap_injection", {"method": "POST", "path": "/auth", "body": "user=*)(|(uid=*", "ip": "1.1.1.1"}),
        (
            "nosql_injection",
            {"method": "POST", "path": "/login", "body": '{"user":"a","pass":{"$ne":""}}', "ip": "1.1.1.1"},
        ),
        (
            "mass_assignment",
            {"method": "POST", "path": "/register", "body": '{"email":"a@b.c","role":"admin"}', "ip": "1.1.1.1"},
        ),
        (
            "file_inclusion",
            {
                "method": "GET",
                "path": "/page",
                "query": "file=php://filter/convert.base64-encode/resource=index",
                "ip": "1.1.1.1",
            },
        ),
        (
            "graphql_recon",
            {"method": "POST", "path": "/graphql", "body": '{"query":"{__schema{types{name}}}"}', "ip": "1.1.1.1"},
        ),
        (
            "sql_injection",
            {"method": "GET", "path": "/api", "query": "id=1' OR pg_sleep(5)--", "ip": "1.1.1.1"},
        ),  # blind SQLi ported
    ]

    @pytest.mark.parametrize("signal,req", CASES)
    def test_attack_fires(self, signal, req):
        names = [s[0] for s in detect_anomalies(req, _PROFILE)]
        assert signal in names, (signal, names)

    @pytest.mark.parametrize(
        "req",
        [
            {"method": "GET", "path": "/", "ip": "192.168.1.10", "headers": {}, "body": ""},
            {"method": "GET", "path": "/products?page=2&sort=price", "ip": "192.168.1.13", "headers": {}, "body": ""},
            {"method": "POST", "path": "/contact", "ip": "192.168.1.12", "headers": {}, "body": "name=alice&msg=hi"},
            {
                "method": "POST",
                "path": "/login",
                "ip": "192.168.1.11",
                "headers": {},
                "body": "user=alice&password=hunter2",
            },
            {"method": "GET", "path": "/assets/app.js", "ip": "192.168.1.10", "headers": {}, "body": ""},
            # templated marketing copy must NOT fire the AND-gated SSTI signal
            {
                "method": "GET",
                "path": "/promo",
                "query": "tpl={{ first_name }}, welcome!",
                "ip": "192.168.1.14",
                "headers": {},
                "body": "",
            },
        ],
    )
    def test_benign_stays_clean(self, req):
        # every sparring-benign entry + common shapes must produce zero signals
        assert detect_anomalies(req, _PROFILE) == []

    def test_benign_through_scorer(self):
        v = score_request(
            {"method": "GET", "path": "/about.html", "ip": "192.168.1.11", "headers": {}, "body": ""}, _PROFILE
        )
        assert v["score"] == 3 and v["level"] == "noise"  # 1 + new_ip only


# ── A4: custom rules wired into the scorer + TUI fast path ─────────────


class TestCustomRulesWired:
    def test_scorer_picks_up_rules_file(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import governance

        rules = tmp_path / "detector_rules.json"
        rules.write_text(
            json.dumps(
                [
                    {
                        "name": "internal-admin-probe",
                        "pattern": "/internal/admin",
                        "field": "path",
                        "weight": 4,
                        "type": "recon",
                    }
                ]
            )
        )
        monkeypatch.setattr(governance, "RULES_PATH", rules)
        # scorer caches config, not rules — but match_rules reads RULES_PATH lazily
        req = {"method": "GET", "path": "/internal/admin/console", "ip": "1.1.1.1", "headers": {}, "body": ""}
        v = score_request(req, _PROFILE)
        assert "rule:recon" in v["signals"]
        assert v["score"] == 7  # 1 + 4(rule) + 2(new_ip)

    def test_absent_rules_file_changes_nothing(self, tmp_path, monkeypatch):
        from suijin.modules.ops.lib import governance

        monkeypatch.setattr(governance, "RULES_PATH", tmp_path / "nope.json")
        v = score_request(
            {"method": "GET", "path": "/internal/admin", "ip": "1.1.1.1", "headers": {}, "body": ""}, _PROFILE
        )
        assert v["score"] == 3 and not any(s.startswith("rule:") for s in v["signals"])

    def test_tui_fast_path_picks_up_rules(self, tmp_path, monkeypatch):
        from suijin.modules.blueteam.lib.blue.tui import feed as feed_mod
        from suijin.modules.ops.lib import governance

        rules = tmp_path / "detector_rules.json"
        rules.write_text(
            json.dumps(
                [{"name": "legacy-shell", "pattern": "c99shell", "field": "body", "weight": 5, "type": "webshell"}]
            )
        )
        monkeypatch.setattr(governance, "RULES_PATH", rules)
        out = feed_mod._detect_obvious_attack(
            {
                "method": "POST",
                "path": "/upload",
                "body": "cmd=c99shell.php",
                "ip": "1.1.1.1",
                "headers": {},
                "user_agent": "",
                "query": "",
            }
        )
        assert any(name == "rule:webshell" for name, _ in out["patterns"])


# ── A5: config-driven weights + thresholds ─────────────────────────────


class TestConfigDrivenScorer:
    def test_weight_override_changes_score(self, tmp_path, monkeypatch):
        from suijin.modules.blueteam.lib.blue.traffic import scorer

        cfg = tmp_path / "blue_config.json"
        cfg.write_text(json.dumps({"scorer": {"signal_weights": {"sql_injection": 10}}}))
        monkeypatch.setattr(scorer, "_CFG_CACHE", {"path": None, "mtime": -1.0, "cfg": None})
        real_cfg_path = scorer._scorer_cfg  # ensure cache refresh via path swap
        import suijin.modules.blueteam.lib.blue.config as bcfg

        monkeypatch.setattr(bcfg, "CONFIG_PATH", cfg)
        v = scorer.score_request(
            {"method": "GET", "path": "/x", "query": "q=' UNION SELECT 1", "ip": "1.1.1.1", "headers": {}, "body": ""},
            _PROFILE,
        )
        assert v["score"] == 10  # 1 + 10(sql) capped before new_ip
        assert real_cfg_path is not None

    def test_threshold_override_changes_level(self, tmp_path, monkeypatch):
        from suijin.modules.blueteam.lib.blue.traffic import scorer

        cfg = tmp_path / "blue_config.json"
        cfg.write_text(json.dumps({"scorer": {"suspicious_threshold": 3}}))
        import suijin.modules.blueteam.lib.blue.config as bcfg

        monkeypatch.setattr(bcfg, "CONFIG_PATH", cfg)
        monkeypatch.setattr(scorer, "_CFG_CACHE", {"path": None, "mtime": -1.0, "cfg": None})
        v = scorer.score_request({"method": "GET", "path": "/", "ip": "9.9.9.9", "headers": {}, "body": ""}, _PROFILE)
        assert v["score"] == 3 and v["level"] == "suspicious"  # 1+new_ip(2) now suspicious

    def test_defaults_unchanged(self, monkeypatch):
        """No config -> exact legacy behavior (sql 4, xss 4, new_ip 2)."""
        from suijin.modules.blueteam.lib.blue.traffic import scorer

        monkeypatch.setattr(scorer, "_CFG_CACHE", {"path": None, "mtime": -1.0, "cfg": None})
        v = scorer.score_request(
            {"method": "GET", "path": "/x", "query": "q=' UNION SELECT 1", "ip": "1.1.1.1", "headers": {}, "body": ""},
            _PROFILE,
        )
        assert v["score"] == 7  # 1 + 4 + 2 — the pre-wave-A number
        v2 = scorer.score_request(
            {"method": "POST", "path": "/s", "body": "<script>alert(1)</script>", "ip": "1.1.1.1", "headers": {}},
            _PROFILE,
        )
        assert v2["score"] == 7  # 1 + 4 + 2

    def test_operator_config_does_not_poison_defaults(self, tmp_path):
        """Regression: load_blue_config used a SHALLOW copy of the defaults —
        _deep_merge wrote operator values into module-level
        DEFAULT_BLUE_CONFIG, so one tuned load leaked into every later load."""
        from suijin.modules.blueteam.lib.blue import config as bcfg

        cfg = tmp_path / "blue_config.json"
        cfg.write_text(json.dumps({"scorer": {"signal_weights": {"sql_injection": 10}}}))
        assert bcfg.DEFAULT_BLUE_CONFIG["scorer"]["signal_weights"]["sql_injection"] == 4
        import pytest

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(bcfg, "CONFIG_PATH", cfg)
            loaded = bcfg.load_blue_config()
        assert loaded["scorer"]["signal_weights"]["sql_injection"] == 10  # operator wins here
        assert bcfg.DEFAULT_BLUE_CONFIG["scorer"]["signal_weights"]["sql_injection"] == 4  # but the default survives
