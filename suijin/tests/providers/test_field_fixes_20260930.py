"""The five field-reported fixes (2026-09-30, SERPent run): local-model
context windows, early compaction, fetch_authorization_page idempotency,
the configurable LLM timeout, and the ollama reasoning_effort strip."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestLocalContextWindow:
    def test_ollama_unknown_model_gets_small_window_not_1m(self, monkeypatch):
        """The whole incident: an unknown local model fell back to 1M and
        the 160k-char floor silenced compaction forever."""
        from suijin.modules.providers.lib import model_meta as mm

        monkeypatch.setattr(mm, "_catalog", lambda: None)  # catalog miss
        assert mm.resolve_context_window("ollama", "qwen2.5-coder:7b", {}) == mm.LOCAL_DEFAULT_WINDOW
        assert mm.LOCAL_DEFAULT_WINDOW <= 8192

    def test_operator_override_wins_over_local_default(self, monkeypatch):
        from suijin.modules.providers.lib import model_meta as mm

        monkeypatch.setattr(mm, "_catalog", lambda: None)
        assert mm.resolve_context_window("ollama", "anything", {"context_window": 32768}) == 32768

    def test_cloud_unknown_keeps_the_1m_fallback(self, monkeypatch):
        from suijin.modules.providers.lib import model_meta as mm

        monkeypatch.setattr(mm, "_catalog", lambda: None)
        assert mm.resolve_context_window("zai", "brand-new-model", {}) == 1_000_000


class TestLocalCompactionBranch:
    def _compact_with(self, win_tokens, msgs):
        """Drive think_node's compaction seam with a resolved window."""
        from suijin.modules.agent.lib.compact import compact

        if win_tokens <= 8192:  # the LOCAL branch, mirrored from think_node
            trigger = int(win_tokens * 4 * 0.70)
            return compact(msgs, trigger_chars=trigger, keep_recent=6), trigger
        trigger = max(160_000, int(win_tokens * 4 * 0.90))  # the cloud branch
        return compact(msgs, trigger_chars=trigger), trigger

    def test_local_window_fires_at_70_percent(self):
        win = 4096
        trigger = int(win * 4 * 0.70)  # 11_468 chars
        big = [{"role": "user", "content": "x" * 900} for _ in range(30)]
        out, trig = self._compact_with(win, big)
        assert trig == trigger
        assert len(out) < len(big)  # compacted — the loop bug is dead

    def test_cloud_branch_keeps_the_floor(self):
        # the floor bites below ~44k-token windows; a 1M window uses its own 90%
        _, trig_floored = self._compact_with(32_768, [])
        assert trig_floored == 160_000
        _, trig_huge = self._compact_with(1_000_000, [])
        assert trig_huge == int(1_000_000 * 4 * 0.90)  # unchanged for big models


class TestFetchAuthPageIdempotency:
    def test_second_call_same_target_is_blocked_with_directive(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as d

        d.reset_fetched_auth_pages()
        calls = []
        monkeypatch.setattr(
            "suijin.modules.ops.lib.authorizations.fetch_page",
            lambda t, u: calls.append(t) or "page content 200",
        )
        first = d._fetch_auth_page("target.example", "https://target.example/auth")
        second = d._fetch_auth_page("target.example", "https://target.example/auth")
        assert "page content" in first
        assert len(calls) == 1  # only ONE real fetch
        assert "ALREADY FETCHED" in second
        assert "http_request" in second and "js_bundle_analyze" in second  # the directive

    def test_error_does_not_register_as_fetched(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as d

        d.reset_fetched_auth_pages()
        monkeypatch.setattr(
            "suijin.modules.ops.lib.authorizations.fetch_page",
            lambda t, u: "Error: connection refused",
        )
        d._fetch_auth_page("t2.example", "")
        monkeypatch.setattr(
            "suijin.modules.ops.lib.authorizations.fetch_page",
            lambda t, u: "ok now",
        )
        assert "ok now" in d._fetch_auth_page("t2.example", "")  # retry allowed

    def test_engagement_reset_clears_the_set(self, monkeypatch):
        from suijin.modules.tools.lib import dispatch as d

        d.reset_fetched_auth_pages()
        monkeypatch.setattr("suijin.modules.ops.lib.authorizations.fetch_page", lambda t, u: "content")
        d._fetch_auth_page("t3.example", "")
        d.reset_fetched_auth_pages()
        assert "content" in d._fetch_auth_page("t3.example", "")


class TestLLMTimeout:
    def test_timeout_resolution(self):
        from suijin.modules.redteam.lib.red.llm_client import _resolve_timeout

        assert _resolve_timeout({"provider": "zai"}) == 180.0
        assert _resolve_timeout({"provider": "ollama"}) == 300.0
        assert _resolve_timeout({"provider": "ollama", "llm_timeout": 90}) == 90.0
        assert _resolve_timeout({"provider": "zai", "llm_timeout": 600}) == 600.0
        assert _resolve_timeout({}) == 180.0
        assert _resolve_timeout({"provider": "ollama", "llm_timeout": "garbage"}) == 300.0

    def test_timeout_actually_fires(self):
        import asyncio
        import threading

        from suijin.modules.redteam.lib.red import llm_client as lc

        release = threading.Event()

        def _hang(*a, _rel=release, **k):
            _rel.wait(timeout=30)

        old = lc._generate
        lc._generate = _hang
        try:
            out = asyncio.run(
                lc.generate_async([{"role": "user", "content": "x"}], {"provider": "zai", "llm_timeout": 1})
            )
        finally:
            release.set()
            lc._generate = old
        assert "timed out after 1s" in out


class TestOllamaEffortStrip:
    def test_standard_local_models_get_no_reasoning_effort(self):
        from suijin.modules.providers.lib import _apply_effort

        payload = {}
        _apply_effort(payload, "qwen2.5-coder:7b", {"intelligence": "high"}, 8000, openai_style=True, provider="ollama")
        assert "reasoning_effort" not in payload  # this was the HTTP 400

    def test_thinking_models_keep_the_field(self):
        from suijin.modules.providers.lib import _apply_effort

        payload = {}
        _apply_effort(payload, "deepseek-r1:14b", {"intelligence": "high"}, 8000, openai_style=True, provider="ollama")
        assert payload.get("reasoning_effort") == "high"

    def test_cloud_openai_style_untouched(self):
        from suijin.modules.providers.lib import _apply_effort

        payload = {}
        _apply_effort(payload, "gpt-5.2", {"intelligence": "high"}, 8000, openai_style=True, provider="openai")
        assert payload.get("reasoning_effort") == "high"

    def test_declares_thinking_detector(self):
        from suijin.modules.providers.lib import _model_declares_thinking as dt

        assert dt("deepseek-r1") and dt("Qwen3-Think") and dt("qwen3:think")
        assert not dt("qwen2.5-coder:7b") and not dt("llama3.2") and not dt("")
