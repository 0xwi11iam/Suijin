"""Tests for the zai (Z.ai GLM) provider branch in tools/providers.py.

All network calls are mocked — no API key needed.
"""
import pytest

from medusa.tools import providers


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "test-key-123")
    providers.reset_usage()


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


CFG = {"provider": "zai", "zai_model": "glm-5.3", "temperature": 0.4, "max_tokens_per_request": 8000}


def _ok_response(model="glm-5.1"):
    return _FakeResponse(200, {
        "choices": [{"message": {"content": "GLM says hello"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": model,
    })


class TestZaiGenerate:
    def test_happy_path(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert out == "GLM says hello"
        assert len(sess.calls) == 1
        call = sess.calls[0]
        assert call["url"] == "https://api.z.ai/api/paas/v4/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer test-key-123"
        assert call["json"]["model"] == "glm-5.3"

    def test_usage_recorded(self, monkeypatch):
        monkeypatch.setattr(providers, "req", _FakeSession(_ok_response()))
        providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        u = providers.get_usage()
        assert u["calls"] == 1
        assert u["input_tokens"] == 10
        assert u["output_tokens"] == 5
        assert u["priced"] is True  # glm-5.1 is in MODEL_PRICING

    def test_model_remap_from_hf_style_id(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], CFG,
                           model_id="zai-org/GLM-5.3", retries=1)
        assert sess.calls[0]["json"]["model"] == "glm-5.3"

    def test_non_glm_model_falls_back_to_default(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], CFG,
                           model_id="gpt-4o", retries=1)
        assert sess.calls[0]["json"]["model"] == "glm-5.3"

    def test_explicit_flash_model_kept(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], CFG,
                           model_id="glm-5.1-flash", retries=1)
        assert sess.calls[0]["json"]["model"] == "glm-5.1-flash"

    def test_invalid_key(self, monkeypatch):
        monkeypatch.setattr(providers, "req", _FakeSession(_FakeResponse(401, text="unauthorized")))
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert "Invalid Z.ai API Key" in out

    def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert "Z.ai API key not set" in out

    def test_reasoning_content_fallback(self, monkeypatch):
        resp = _FakeResponse(200, {
            "choices": [{"message": {"reasoning_content": "chain of thought answer"}}],
            "usage": {},
        })
        monkeypatch.setattr(providers, "req", _FakeSession(resp))
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert out == "chain of thought answer"


class TestZaiPricing:
    def test_glm_models_priced(self):
        for m in ("glm-5.3", "glm-5.3-flash", "glm-5.1", "glm-5.1-flash", "glm-4.7", "glm-4.7-flash"):
            assert providers._price_for(m) is not None, m

    def test_unknown_model_unpriced(self):
        # Not in MODEL_PRICING and no substring match → _record_usage falls
        # back to DEFAULT_RATE and flags USAGE["priced"] = False.
        assert providers._price_for("totally-unknown-model") is None
