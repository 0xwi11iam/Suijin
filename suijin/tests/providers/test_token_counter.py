"""Genuine token counting — API-reported vs estimated, never silent zero."""

from suijin.modules.providers.lib import USAGE, estimate_tokens, record_missing_usage, reset_usage


class TestEstimator:
    def test_word_aware(self):
        # ~10 words of plain english -> roughly 13 tokens, NOT chars/4 (~15) — sanity band
        t = estimate_tokens("the quick brown fox jumps over the lazy dog again")
        assert 8 <= t <= 20

    def test_json_and_code(self):
        assert estimate_tokens('{"tool": "nmap_scan", "args": {"target": "10.0.0.1"}}') > 10

    def test_cjk(self):
        assert estimate_tokens("設定") >= 2

    def test_empty(self):
        assert estimate_tokens("") == 0 and estimate_tokens(None) == 0


class TestAttribution:
    def test_missing_usage_estimated_not_zero(self):
        reset_usage()
        record_missing_usage(
            [{"role": "user", "content": "run nmap on the target please now thanks"}],
            '{"action": "use_tool", "tool_name": "nmap_scan"}',
            "deepseek",
            "deepseek-chat",
        )
        u = USAGE
        assert u["calls"] == 1 and u["estimated_calls"] == 1 and u["api_reported_calls"] == 0
        assert u["input_tokens"] > 0 and u["output_tokens"] > 0  # estimated, not silently zero
        assert u["by_provider"]["deepseek"]["calls"] == 1

    def test_reported_usage_attributed(self):
        from suijin.modules.providers.lib import _record_usage

        reset_usage()
        _record_usage("zai", "glm-4.7", 100, 50)
        u = USAGE
        assert u["api_reported_calls"] == 1 and u["estimated_calls"] == 0
        assert u["input_tokens"] == 100 and u["output_tokens"] == 50
        assert u["by_provider"]["zai"]["cost_usd"] > 0
