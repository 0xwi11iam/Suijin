"""model_meta — the context-window resolver (config override → models.dev
fetch → 1M fallback). Never raises, never blocks a call."""

import json
import time

import pytest

import suijin.modules.providers.lib.model_meta as mm


@pytest.fixture(autouse=True)
def _reset_negative(monkeypatch, tmp_path):
    """Hermetic: no network, no workspace cache leaks, no backoff state."""
    monkeypatch.setattr(mm, "_negative_cache", False)
    monkeypatch.setattr(mm, "_last_fetch_attempt", 0.0)
    monkeypatch.setattr(mm, "_cache_path", lambda: tmp_path / "caches" / "models.dev.json")
    yield


class TestResolutionOrder:
    def test_config_override_wins(self):
        w = mm.resolve_context_window("zai", "glm-5.3", {"context_window": 131072})
        assert w == 131072

    def test_unknown_offline_falls_back_to_1m(self, monkeypatch):
        monkeypatch.setattr(mm, "_fetch_catalog", lambda: None)
        assert mm.resolve_context_window("zai", "glm-5.3", {}) == mm.DEFAULT_CONTEXT_WINDOW == 1_000_000

    def test_cached_catalog_resolves_without_network(self, tmp_path, monkeypatch):
        p = tmp_path / "caches" / "models.dev.json"
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps(
                {
                    "_fetched_at": time.time(),
                    "providers": {"zai": {"models": {"glm-5.3": {"context_length": 200000}}}},
                }
            )
        )
        monkeypatch.setattr(mm, "_fetch_catalog", lambda: pytest.fail("network must not be touched"))
        assert mm.resolve_context_window("zai", "glm-5.3", {}) == 200_000

    def test_stale_cache_refetches(self, tmp_path, monkeypatch):
        p = tmp_path / "caches" / "models.dev.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"_fetched_at": 0.0, "providers": {}}))  # ancient
        fetched = {
            "_fetched_at": time.time(),
            "providers": {"groq": {"models": {"llama-3.3-70b-versatile": {"limit": 131072}}}},
        }
        monkeypatch.setattr(mm, "_fetch_catalog", lambda: fetched)
        assert mm.resolve_context_window("groq", "llama-3.3-70b-versatile", {}) == 131_072

    def test_substring_model_match(self, monkeypatch):
        cat = {
            "_fetched_at": time.time(),
            "providers": {"xai": {"models": {"grok-4.1-fast-2026-01": {"context_length": 256000}}}},
        }
        monkeypatch.setattr(mm, "_catalog", lambda: cat)
        assert mm.resolve_context_window("xai", "grok-4.1-fast", {}) == 256_000

    def test_custom_provider_prefix_stripped(self, monkeypatch):
        cat = {"_fetched_at": time.time(), "providers": {"mybox": {"models": {"m-1": {"context_length": 32768}}}}}
        monkeypatch.setattr(mm, "_catalog", lambda: cat)
        assert mm.resolve_context_window("custom:mybox", "m-1", {}) == 32_768

    def test_slash_model_ids_split(self, monkeypatch):
        cat = {"_fetched_at": time.time(), "providers": {"openai": {"models": {"gpt-4o": {"context_length": 128000}}}}}
        monkeypatch.setattr(mm, "_catalog", lambda: cat)
        assert mm.resolve_context_window("openrouter", "openai/gpt-4o", {}) == 128_000


class TestFetchBehavior:
    def test_failed_fetch_backs_off(self, monkeypatch):
        calls = []

        def boom():
            calls.append(1)
            return None

        monkeypatch.setattr(mm, "_fetch_catalog", boom)
        monkeypatch.setattr(mm, "_negative_cache", True)
        monkeypatch.setattr(mm, "_last_fetch_attempt", time.time())
        # inside the backoff window: no call, straight to fallback
        assert mm.resolve_context_window("p", "m", {}) == 1_000_000
        assert calls == []

    def test_corrupt_cache_is_ignored(self, tmp_path, monkeypatch):
        p = tmp_path / "caches" / "models.dev.json"
        p.parent.mkdir(parents=True)
        p.write_text("{corrupt")
        monkeypatch.setattr(mm, "_fetch_catalog", lambda: None)
        assert mm.resolve_context_window("p", "m", {}) == 1_000_000


class TestHelpers:
    def test_window_chars(self, monkeypatch):
        monkeypatch.setattr(mm, "resolve_context_window", lambda p, m, c: 1000)
        assert mm.window_chars("p", "m") == 4000

    def test_window_status_sources(self, monkeypatch):
        s = mm.window_status("zai", "glm-5.3", {"context_window": 1000})
        assert s["source"] == "config override"
        monkeypatch.setattr(mm, "_fetch_catalog", lambda: None)
        s2 = mm.window_status("p", "m", {})
        assert s2["source"].startswith("fallback")
