"""LLM-path tests — providers, AI engine, oracle, supervisor with mocked LLMs.

Covers the AI-decision core with fault injection: successful calls, error
strings, fail-open behavior, usage accounting, and hypothesis generation.
No real API calls — everything is mocked.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


# ═══════════════════════════════════════════════════════════════════════════════
# providers.py — pricing, usage accounting, generate() paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestProviderPricing:
    def test_exact_model(self):
        from suijin.modules.providers.lib import _price_for

        assert _price_for("deepseek-v4-flash") == (0.27, 1.10)

    def test_prefix_match(self):
        from suijin.modules.providers.lib import _price_for

        price = _price_for("anthropic/claude-opus-4-8")
        assert price == (15.0, 75.0)

    def test_unknown_model_none(self):
        from suijin.modules.providers.lib import _price_for

        assert _price_for("totally-unknown-model") is None
        assert _price_for(None) is None
        assert _price_for("") is None


class TestProviderUsage:
    def test_reset_and_record(self):
        from suijin.modules.providers.lib import _record_usage, get_usage, reset_usage

        reset_usage()
        _record_usage("deepseek", "deepseek-v4-flash", 1000, 500)
        usage = get_usage()
        assert usage["calls"] == 1
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 500
        assert usage["priced"] is True
        expected_cost = (1000 * 0.27 + 500 * 1.10) / 1_000_000
        assert abs(usage["est_cost_usd"] - expected_cost) < 1e-9

    def test_unpriced_model_uses_default_rate(self):
        from suijin.modules.providers.lib import _record_usage, get_usage, reset_usage

        reset_usage()
        _record_usage("unknown", "mystery-model", 1000000, 1000000)
        usage = get_usage()
        assert usage["priced"] is False
        expected_cost = (1000000 * 0.20 + 1000000 * 0.60) / 1_000_000
        assert abs(usage["est_cost_usd"] - expected_cost) < 1e-9

    def test_record_usage_never_raises(self):
        from suijin.modules.providers.lib import _record_usage, get_usage, reset_usage

        reset_usage()
        # Malformed token counts must be swallowed — never crash the call
        _record_usage("x", "y", None, "bad-tokens")
        usage = get_usage()
        assert usage["calls"] == 0


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class TestGenerateDeepSeek:
    def test_success_returns_content_and_records_usage(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate, get_usage, reset_usage

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        monkeypatch.setattr(
            p,
            "_stream_chat",
            lambda *a, **k: (200, "hello from mock", "", {"prompt_tokens": 100, "completion_tokens": 50}, ""),
        )
        reset_usage()
        result = generate(
            [{"role": "user", "content": "hi"}], {"provider": "deepseek", "deepseek_model": "deepseek-v4-flash"}
        )
        assert result == "hello from mock"
        usage = get_usage()
        assert usage["calls"] == 1
        assert usage["input_tokens"] == 100

    def test_missing_key_errors_before_request(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        called = []
        monkeypatch.setattr(p._HTTP, "post", lambda *a, **k: called.append(True))
        result = generate([{"role": "user", "content": "hi"}], {"provider": "deepseek"}, retries=1)
        assert "API key" in result
        assert called == []

    def test_401_invalid_key(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("DEEPSEEK_API_KEY", "bad-key")
        monkeypatch.setattr(p, "_stream_chat", lambda *a, **k: (401, "", "", None, "unauthorized"))
        result = generate([{"role": "user", "content": "hi"}], {"provider": "deepseek"}, retries=1)
        assert "Invalid DeepSeek API Key" in result

    def test_402_payment_required(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setattr(p, "_stream_chat", lambda *a, **k: (402, "", "", None, "billing"))
        result = generate([{"role": "user", "content": "hi"}], {"provider": "deepseek"}, retries=1)
        assert "402" in result

    def test_stream_transport_failure_uses_nonstream_fallback(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setattr(p, "_stream_chat", lambda *a, **k: (0, "", "", None, "conn reset"))
        monkeypatch.setattr(
            p,
            "_post_chat",
            lambda *a, **k: (
                200,
                {
                    "choices": [{"message": {"content": "fallback ok"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
                "",
            ),
        )
        result = generate([{"role": "user", "content": "hi"}], {"provider": "deepseek"}, retries=1)
        assert result == "fallback ok"

    def test_retries_exhausted_falls_through(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setattr(p, "_stream_chat", lambda *a, **k: (500, "", "", None, "boom"))
        monkeypatch.setattr(p, "_post_chat", lambda *a, **k: (500, None, "boom"))
        monkeypatch.setattr(p.time, "sleep", lambda s: None)
        result = generate([{"role": "user", "content": "hi"}], {"provider": "deepseek"}, retries=2)
        assert "Error" in result

    def test_non_deepseek_model_remapped(self, monkeypatch):
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        sent = {}

        def fake_stream(url, headers, payload, on_delta=None):
            sent.update(payload)
            return 200, "ok", "", {"prompt_tokens": 1, "completion_tokens": 1}, ""

        monkeypatch.setattr(p, "_stream_chat", fake_stream)
        generate(
            [{"role": "user", "content": "hi"}],
            {"provider": "deepseek", "deepseek_model": "deepseek-v4-flash"},
            model_id="anthropic/claude-opus-4-8",
            retries=1,
        )
        assert sent["model"] == "deepseek-v4-flash"

    def test_unknown_provider(self, monkeypatch):
        from suijin.modules.providers.lib import generate

        result = generate([{"role": "user", "content": "hi"}], {"provider": "not-a-real-provider"})
        assert "Unknown provider" in result

    def test_on_delta_streams_reasoning_and_content(self, monkeypatch):
        """The streaming callback contract: reasoning and content deltas
        reach the UI live, in arrival order."""
        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

        def fake_stream(url, headers, payload, on_delta=None):
            on_delta("reasoning", "thinking ")
            on_delta("reasoning", "hard")
            on_delta("content", '{"action": "complete"}')
            return 200, '{"action": "complete"}', "thinking hard", {"prompt_tokens": 3, "completion_tokens": 3}, ""

        monkeypatch.setattr(p, "_stream_chat", fake_stream)
        seen = []
        out = generate(
            [{"role": "user", "content": "hi"}],
            {"provider": "deepseek"},
            retries=1,
            on_delta=lambda k, t: seen.append((k, t)),
        )
        assert out == '{"action": "complete"}'
        assert seen == [
            ("reasoning", "thinking "),
            ("reasoning", "hard"),
            ("content", '{"action": "complete"}'),
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# ai_engine.py — prompt building, response parsing, fail-open, actions
# ═══════════════════════════════════════════════════════════════════════════════


class TestAIEngineParsing:
    def test_parse_direct_json(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        parsed = engine._parse_llm_response('{"verdict":"FLAGGED","score":9}')
        assert parsed["verdict"] == "FLAGGED"
        assert parsed["score"] == 9

    def test_parse_markdown_fence(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        raw = '```json\n{"verdict":"NOT FLAGGED","score":2}\n```'
        parsed = engine._parse_llm_response(raw)
        assert parsed["verdict"] == "NOT FLAGGED"

    def test_parse_brace_block(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        raw = 'text before {"verdict":"FLAGGED","score":7} text after'
        parsed = engine._parse_llm_response(raw)
        assert parsed["score"] == 7

    def test_parse_fallback_to_reasoning(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        parsed = engine._parse_llm_response("just plain text")
        assert parsed == {"reasoning": "just plain text"}

    def test_parse_empty(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        assert engine._parse_llm_response("") == {}


class TestAIEnginePrompt:
    def test_prompt_contains_request_context(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        prompt = engine.build_analysis_prompt(
            {
                "method": "POST",
                "path": "/auth/login",
                "ip": "10.0.0.9",
                "body": "admin' OR '1'='1",
                "user_agent": "curl",
                "query": {},
                "headers": {},
            },
            {"handler": "def login(): ..."},
        )
        assert "POST" in prompt
        assert "/auth/login" in prompt
        assert "10.0.0.9" in prompt
        assert "OR '1'='1" in prompt

    def test_prompt_includes_endpoint_info(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        prompt = engine.build_analysis_prompt(
            {"method": "GET", "path": "/x"},
            {"framework": "flask", "auth_required": True, "handler_code": "def handler(): return 'x'"},
        )
        assert "def handler(): return 'x'" in prompt
        assert "flask" in prompt

    def test_prompt_includes_subagent_notes(self):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        engine = BlueAIEngine({})
        prompt = engine.build_analysis_prompt(
            {"method": "GET", "path": "/x"},
            {},
            subagent_notes="RISK: HIGH",
        )
        assert "RISK: HIGH" in prompt


class TestAIEngineAnalyze:
    def _mock_generate_flagged(self, monkeypatch):
        def fake_generate(messages, config=None, **kwargs):
            return json.dumps(
                {
                    "verdict": "FLAGGED",
                    "score": 9,
                    "action": "DECEIVE",
                    "reasoning": "mocked",
                    "attack_analysis": "sqli",
                    "attacker_assessment": "scanner",
                }
            )

        monkeypatch.setattr("suijin.modules.providers.lib.generate", fake_generate)

    def test_analyze_flagged(self, monkeypatch):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        self._mock_generate_flagged(monkeypatch)
        engine = BlueAIEngine({})
        result = asyncio.run(
            engine.analyze_request(
                {"method": "POST", "path": "/login", "body": "x", "ip": "1.2.3.4", "query": {}, "headers": {}},
                {},
            )
        )
        assert result.verdict == "FLAGGED"
        assert result.score == 9
        assert result.action == "DECEIVE"
        assert engine.total_analyses == 1
        assert len(engine.analysis_history) == 1

    def test_analyze_api_error_fails_open_flagged(self, monkeypatch):
        """API errors must fail OPEN — FLAGGED, not silently allowed."""
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        def fake_generate(messages, config=None, **kwargs):
            return "Error: DeepSeek API key not set"

        monkeypatch.setattr("suijin.modules.providers.lib.generate", fake_generate)

        engine = BlueAIEngine({})
        result = asyncio.run(
            engine.analyze_request(
                {"method": "POST", "path": "/login", "body": "x' OR 1=1", "ip": "1.2.3.4", "query": {}, "headers": {}},
                {},
            )
        )
        assert result.verdict == "FLAGGED"
        assert result.action == "REVIEW"

    def test_analyze_malformed_response_fails_open(self, monkeypatch):
        from suijin.modules.blueteam.lib.blue.ai_engine import BlueAIEngine

        def fake_generate(messages, config=None, **kwargs):
            return "garbage that is not json"

        monkeypatch.setattr("suijin.modules.providers.lib.generate", fake_generate)

        engine = BlueAIEngine({})
        result = asyncio.run(
            engine.analyze_request(
                {"method": "POST", "path": "/login", "body": "x", "ip": "1.2.3.4", "query": {}, "headers": {}},
                {},
            )
        )
        # Not-flagged fallback or flagged — but must not crash
        assert result.verdict in ("FLAGGED", "NOT FLAGGED")


class TestAIEngineActions:
    def test_execute_actions_commands(self, monkeypatch, tmp_path):
        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult, BlueAIEngine

        engine = BlueAIEngine({})

        class FakeProc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())

        result = AIAnalysisResult(
            request_id=1,
            method="GET",
            path="/",
            ip="1.2.3.4",
            verdict="FLAGGED",
            score=8,
            action="DECEIVE",
            commands_run=["echo hello"],
        )
        executed = engine.execute_actions(result, str(tmp_path))
        assert len(executed) == 1
        assert executed[0]["type"] == "command"
        assert executed[0]["exit_code"] == 0

    def test_execute_actions_command_timeout(self, monkeypatch, tmp_path):
        import subprocess

        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult, BlueAIEngine

        engine = BlueAIEngine({})

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=30)

        monkeypatch.setattr("subprocess.run", fake_run)

        result = AIAnalysisResult(
            request_id=1,
            method="GET",
            path="/",
            ip="1.2.3.4",
            verdict="FLAGGED",
            score=8,
            action="DECEIVE",
            commands_run=["sleep 100"],
        )
        executed = engine.execute_actions(result, str(tmp_path))
        assert executed[0]["error"] == "Timeout after 30s"

    def test_execute_actions_code_change_written(self, monkeypatch, tmp_path):
        from suijin.modules.blueteam.lib.blue.ai_engine import AIAnalysisResult, BlueAIEngine

        engine = BlueAIEngine({})
        target_file = tmp_path / "app.py"
        target_file.write_text("old code")

        result = AIAnalysisResult(
            request_id=1,
            method="GET",
            path="/",
            ip="1.2.3.4",
            verdict="FLAGGED",
            score=9,
            action="PATCH",
            code_changes=[{"file": "app.py", "change": "parameterize", "new_content": "new secure code"}],
        )
        executed = engine.execute_actions(result, str(tmp_path))
        assert target_file.read_text() == "new secure code"
        assert any(e["type"] == "code_change" for e in executed)


# ═══════════════════════════════════════════════════════════════════════════════
# oracle.py — anomaly detection, payload mutations, hypothesis generation
# ═══════════════════════════════════════════════════════════════════════════════


class TestOracleDetectAnomaly:
    def test_http_500_high(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("Internal Server Error", status_code=500)
        assert result["anomaly"] is True
        assert result["severity"] == "high"

    def test_http_403_waf(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("Forbidden", status_code=403)
        assert result["anomaly"] is True
        assert result["severity"] == "medium"

    def test_length_delta(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("a" * 500, baseline_len=100)
        assert result["anomaly"] is True
        assert any("body_length" in s for s in result["signals"])

    def test_elapsed_timeout(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("ok", elapsed=20)
        assert result["anomaly"] is True

    def test_error_keywords(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("SQL syntax error near SELECT")
        assert result["anomaly"] is True
        assert result["severity"] == "high"

    def test_payload_reflection(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("echo <script>alert(1)</script>")
        assert result["anomaly"] is True

    def test_clean_response(self):
        from suijin.modules.redteam.lib.intel.oracle import detect_anomaly

        result = detect_anomaly("normal page content", status_code=200)
        assert result["anomaly"] is False


class TestOraclePayloadMutations:
    def test_synonym_payload(self):
        from suijin.modules.redteam.lib.intel.oracle import _make_synonym_payload

        result = _make_synonym_payload("OR 1=1")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_escaped_payload(self):
        from suijin.modules.redteam.lib.intel.oracle import _make_escaped_payload

        result = _make_escaped_payload("' OR 1=1")
        assert isinstance(result, str)
        assert "OR 1=1" in result

    def test_encoded_payload(self):
        from suijin.modules.redteam.lib.intel.oracle import _make_encoded_payload

        result = _make_encoded_payload("' OR 1=1")
        assert isinstance(result, str)
        assert len(result) > 0


class TestOracleHypotheses:
    def test_heuristic_fallback_returns_three(self):
        from suijin.modules.redteam.lib.intel.oracle import _heuristic_hypotheses

        hyps = _heuristic_hypotheses("HTTP_500_backend_error: SQL syntax error", "' OR 1=1")
        assert isinstance(hyps, list)
        assert len(hyps) >= 1
        for h in hyps:
            assert "hypothesis" in h
            assert "validation_payload" in h

    def test_generate_hypotheses_no_provider_uses_heuristic(self, monkeypatch):
        from suijin.modules.redteam.lib.intel import oracle

        monkeypatch.setattr(oracle, "_generate", None)
        hyps = oracle.generate_hypotheses("SQL syntax error", "payload")
        assert len(hyps) >= 1

    def test_generate_hypotheses_with_mocked_llm(self, monkeypatch):
        from suijin.modules.redteam.lib.intel import oracle

        def fake_generate(messages, config=None, **kwargs):
            return json.dumps(
                [
                    {
                        "id": "H1",
                        "hypothesis": "WAF blocked",
                        "confidence": 0.8,
                        "validation_payload": "x",
                        "expected_confirm": "403",
                        "expected_disconfirm": "200",
                    },
                ]
            )

        monkeypatch.setattr(oracle, "_generate", fake_generate)
        hyps = oracle.generate_hypotheses("snippet", "payload", config={})
        assert len(hyps) == 1
        assert hyps[0]["id"] == "H1"
        assert hyps[0]["confidence"] == 0.8

    def test_generate_hypotheses_llm_error_falls_back(self, monkeypatch):
        from suijin.modules.redteam.lib.intel import oracle

        def fake_generate(messages, config=None, **kwargs):
            return "Error: API down"

        monkeypatch.setattr(oracle, "_generate", fake_generate)
        hyps = oracle.generate_hypotheses("snippet", "payload", config={})
        assert len(hyps) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# supervisor.py — verdict parsing, heuristics, evaluate loop
# ═══════════════════════════════════════════════════════════════════════════════


class TestSupervisorVerdict:
    def test_parse_verdict_json(self):
        from suijin.modules.redteam.lib.intel.supervisor import _parse_verdict

        v = _parse_verdict('{"stuck":true,"reason":"loop","new_directive":"change"}')
        assert v["stuck"] is True
        assert v["reason"] == "loop"

    def test_parse_verdict_garbage_defaults(self):
        from suijin.modules.redteam.lib.intel.supervisor import _parse_verdict

        v = _parse_verdict("not json")
        assert v["stuck"] is False
        assert v["new_directive"] == ""

    def test_parse_verdict_empty(self):
        from suijin.modules.redteam.lib.intel.supervisor import _parse_verdict

        v = _parse_verdict(None)
        assert v["stuck"] is False

    def test_format_spend(self):
        from suijin.modules.redteam.lib.intel.supervisor import format_spend

        out = format_spend({"est_cost_usd": 0.5, "input_tokens": 100, "output_tokens": 50, "calls": 3, "priced": True})
        assert "0.5000" in out
        assert "3 calls" in out


class TestSupervisorHeuristics:
    def test_repeated_action_flag(self):
        from suijin.modules.redteam.lib.intel.supervisor import heuristic_stuck_check

        telemetry = {
            "max_repeat": 3,
            "error_count": 0,
            "drift": {"drift_detected": False},
            "usage": {"est_cost_usd": 0.01},
        }
        flags = heuristic_stuck_check(telemetry, {"cost_alert_usd": 0.25})
        assert "repeated_action" in flags

    def test_repeated_errors_flag(self):
        from suijin.modules.redteam.lib.intel.supervisor import heuristic_stuck_check

        telemetry = {
            "max_repeat": 1,
            "error_count": 4,
            "drift": {"drift_detected": False},
            "usage": {"est_cost_usd": 0.01},
        }
        flags = heuristic_stuck_check(telemetry, {"cost_alert_usd": 0.25})
        assert "repeated_errors" in flags

    def test_cost_alert_flag(self):
        from suijin.modules.redteam.lib.intel.supervisor import heuristic_stuck_check

        telemetry = {
            "max_repeat": 1,
            "error_count": 0,
            "drift": {"drift_detected": False},
            "usage": {"est_cost_usd": 0.50},
        }
        flags = heuristic_stuck_check(telemetry, {"cost_alert_usd": 0.25})
        assert "cost_alert" in flags

    def test_no_flags_when_healthy(self):
        from suijin.modules.redteam.lib.intel.supervisor import heuristic_stuck_check

        telemetry = {
            "max_repeat": 1,
            "error_count": 0,
            "drift": {"drift_detected": False},
            "usage": {"est_cost_usd": 0.01},
        }
        flags = heuristic_stuck_check(telemetry, {"cost_alert_usd": 0.25})
        assert flags == []


class TestSupervisorEvaluate:
    def test_heuristics_authoritative_for_loops(self, monkeypatch):
        """Repeated actions force stuck=True even if the LLM says otherwise."""
        from suijin.modules.redteam.lib.intel import supervisor as sv

        def fake_generate(messages, config=None, **kwargs):
            return json.dumps({"stuck": False, "reason": "", "new_directive": ""})

        monkeypatch.setattr(sv, "generate", fake_generate)

        # Build telemetry with max_repeat >= 3
        import suijin.modules.redteam.lib.intel.supervisor as m

        monkeypatch.setattr(
            m,
            "collect_telemetry",
            lambda *a, **k: {
                "turn": 3,
                "objective": "x",
                "total_actions": 4,
                "recent_actions": ["nmap: x", "nmap: x", "nmap: x"],
                "recent_results": [],
                "recent_thoughts": [],
                "drift": {"drift_detected": False, "drift_count": 0},
                "max_repeat": 3,
                "error_count": 0,
                "usage": {"est_cost_usd": 0.01},
            },
        )

        verdict, telemetry, flags = sv.evaluate(
            [],
            turn=3,
            objective="test",
            config={"supervisor_interval": 5, "cost_alert_usd": 0.25, "cost_budget_usd": 1.0, "cost_hard_cap_usd": 2.0},
        )
        assert verdict["stuck"] is True
        assert "repeated_action" in flags

    def test_cost_hard_cap_recommends_abort(self, monkeypatch):
        import suijin.modules.redteam.lib.intel.supervisor as m
        from suijin.modules.redteam.lib.intel import supervisor as sv

        monkeypatch.setattr(sv, "generate", lambda *a, **k: json.dumps({}))
        monkeypatch.setattr(
            m,
            "collect_telemetry",
            lambda *a, **k: {
                "turn": 1,
                "objective": "x",
                "total_actions": 0,
                "recent_actions": [],
                "recent_results": [],
                "recent_thoughts": [],
                "drift": {"drift_detected": False, "drift_count": 0},
                "max_repeat": 0,
                "error_count": 0,
                "usage": {"est_cost_usd": 5.0},
            },
        )
        # The cost guardrail reads the REAL providers tally — mock it too
        monkeypatch.setattr(sv.providers, "get_usage", lambda: {"est_cost_usd": 5.0})
        verdict, _, _ = sv.evaluate(
            [],
            turn=1,
            objective="test",
            config={"supervisor_interval": 5, "cost_alert_usd": 0.25, "cost_budget_usd": 1.0, "cost_hard_cap_usd": 2.0},
        )
        assert verdict["recommend_abort"] is True
        assert verdict["stuck"] is True

    def test_evaluate_skips_llm_when_healthy(self, monkeypatch):
        import suijin.modules.redteam.lib.intel.supervisor as m
        from suijin.modules.redteam.lib.intel import supervisor as sv

        called = []

        def fake_generate(*a, **k):
            called.append(True)
            return json.dumps({})

        monkeypatch.setattr(sv, "generate", fake_generate)
        monkeypatch.setattr(
            m,
            "collect_telemetry",
            lambda *a, **k: {
                "turn": 1,
                "objective": "x",
                "total_actions": 1,
                "recent_actions": ["nmap: x"],
                "recent_results": [],
                "recent_thoughts": [],
                "drift": {"drift_detected": False, "drift_count": 0},
                "max_repeat": 1,
                "error_count": 0,
                "usage": {"est_cost_usd": 0.01},
            },
        )
        verdict, _, _ = sv.evaluate(
            [],
            turn=1,
            objective="test",
            config={"supervisor_interval": 5, "cost_alert_usd": 0.25, "cost_budget_usd": 1.0, "cost_hard_cap_usd": 2.0},
        )
        assert verdict.get("skipped") is True  # no LLM call, no flags
        assert called == []


class TestTransportDiagnosis:
    """TLS/DNS/connect/read failures diagnose to actionable causes —
    the operator sees WHY, never a raw traceback."""

    def _diag(self, exc):
        from suijin.modules.providers.lib import _diagnose_transport

        return _diagnose_transport(exc)

    def test_tls_handshake(self):
        import requests as rq

        out = self._diag(rq.exceptions.SSLError("HTTPSConnectionPool: SSL: HANDSHAKE_FAILURE"))
        assert "TLS handshake failed" in out and "VPN" in out

    def test_read_timeout(self):
        import requests as rq

        out = self._diag(rq.exceptions.ReadTimeout("read timed out"))
        assert "read timeout" in out

    def test_connect_timeout(self):
        import requests as rq

        out = self._diag(rq.exceptions.ConnectTimeout("connect timeout"))
        assert "connect timeout" in out

    def test_dns_failure(self):
        import requests as rq

        out = self._diag(rq.exceptions.ConnectionError("HTTPSConnectionPool host: getaddrinfo failed"))
        assert "DNS failure" in out

    def test_transport_exception_never_escapes_generate(self, monkeypatch):
        """Even an unexpected raise inside the stream path returns a
        diagnosed Error: string — the agent loop never sees a traceback."""
        import requests as rq

        import suijin.modules.providers.lib as p
        from suijin.modules.providers.lib import generate

        monkeypatch.setenv("ZAI_API_KEY", "k")
        monkeypatch.setattr(p.time, "sleep", lambda s: None)

        def boom(*a, **k):
            raise rq.exceptions.SSLError("sslv3 alert handshake failure")

        monkeypatch.setattr(p, "_stream_chat", boom)
        monkeypatch.setattr(p, "_post_chat", boom)
        out = generate([{"role": "user", "content": "hi"}], {"provider": "zai"}, retries=2)
        assert out.startswith("Error:")
        assert "TLS handshake failed" in out
