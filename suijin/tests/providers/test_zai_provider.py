"""Tests for the zai (Z.ai GLM) provider branch in tools/providers.py.

Covers the dual-endpoint design: `zai_endpoint` config selects the GLM Coding
Plan subscription endpoint ("coding", DEFAULT — burns plan credits) or the
pay-as-you-go endpoint ("paas" — per-token USD). All network calls are mocked
— no API key needed.
"""

import pytest

from suijin.modules.providers import lib as providers


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


CODING_URL = "https://api.z.ai/api/coding/paas/v4/chat/completions"
PAAS_URL = "https://api.z.ai/api/paas/v4/chat/completions"

CFG = {"provider": "zai", "zai_model": "glm-5.3", "temperature": 0.4, "max_tokens_per_request": 8000}


def _ok_response(model="glm-5.3"):
    return _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "GLM says hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": model,
        },
    )


class TestZaiEndpoints:
    """zai_endpoint picks the billing surface; Coding Plan is the default."""

    def test_default_is_coding_plan(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert out == "GLM says hello"
        assert sess.calls[0]["url"] == CODING_URL

    def test_explicit_coding_endpoint(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], {**CFG, "zai_endpoint": "coding"}, retries=1)
        assert sess.calls[0]["url"] == CODING_URL

    def test_paas_endpoint_selected(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], {**CFG, "zai_endpoint": "paas"}, retries=1)
        assert sess.calls[0]["url"] == PAAS_URL

    def test_endpoint_case_insensitive(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], {**CFG, "zai_endpoint": "  PaaS "}, retries=1)
        assert sess.calls[0]["url"] == PAAS_URL

    def test_custom_base_url_passthrough(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate(
            [{"role": "user", "content": "hi"}], {**CFG, "zai_endpoint": "https://proxy.example.com/v1"}, retries=1
        )
        assert sess.calls[0]["url"] == "https://proxy.example.com/v1/chat/completions"

    def test_unknown_endpoint_falls_back_to_coding(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], {**CFG, "zai_endpoint": "garbage"}, retries=1)
        # never silently hits pay-as-you-go with a plan key
        assert sess.calls[0]["url"] == CODING_URL

    def test_url_constants_match_z_ai_docs(self):
        # Coding Plan endpoint from https://docs.z.ai/devpack/tool/others —
        # a typo here bills the wrong surface, so pin it.
        assert providers.ZAI_CODING_BASE_URL == "https://api.z.ai/api/coding/paas/v4"
        assert providers.ZAI_PAAS_BASE_URL == "https://api.z.ai/api/paas/v4"
        assert providers.ZAI_ENDPOINTS["coding"] == providers.ZAI_CODING_BASE_URL
        assert providers.ZAI_ENDPOINTS["paas"] == providers.ZAI_PAAS_BASE_URL


class TestZaiGenerate:
    def test_happy_path(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert out == "GLM says hello"
        assert len(sess.calls) == 1
        call = sess.calls[0]
        assert call["url"] == CODING_URL
        assert call["headers"]["Authorization"] == "Bearer test-key-123"
        assert call["json"]["model"] == "glm-5.3"

    def test_usage_recorded(self, monkeypatch):
        monkeypatch.setattr(providers, "req", _FakeSession(_ok_response()))
        providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        u = providers.get_usage()
        assert u["calls"] == 1
        assert u["input_tokens"] == 10
        assert u["output_tokens"] == 5
        assert u["priced"] is True  # glm-5.3 is in MODEL_PRICING

    def test_model_remap_from_hf_style_id(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], CFG, model_id="zai-org/GLM-5.3", retries=1)
        assert sess.calls[0]["json"]["model"] == "glm-5.3"

    def test_non_glm_model_falls_back_to_default(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], CFG, model_id="gpt-4o", retries=1)
        assert sess.calls[0]["json"]["model"] == "glm-5.3"

    def test_turbo_model_kept(self, monkeypatch):
        sess = _FakeSession(_ok_response())
        monkeypatch.setattr(providers, "req", sess)
        providers.generate([{"role": "user", "content": "hi"}], CFG, model_id="glm-5-turbo", retries=1)
        assert sess.calls[0]["json"]["model"] == "glm-5-turbo"

    def test_invalid_key(self, monkeypatch):
        monkeypatch.setattr(providers, "req", _FakeSession(_FakeResponse(401, text="unauthorized")))
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert "Invalid Z.ai API Key" in out

    def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert "Z.ai API key not set" in out

    def test_reasoning_content_fallback(self, monkeypatch):
        resp = _FakeResponse(
            200,
            {
                "choices": [{"message": {"reasoning_content": "chain of thought answer"}}],
                "usage": {},
            },
        )
        monkeypatch.setattr(providers, "req", _FakeSession(resp))
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=1)
        assert out == "chain of thought answer"

    def test_timeout_message(self, monkeypatch):
        sess = _FakeSession(_FakeResponse(500, text="boom"))
        monkeypatch.setattr(providers, "req", sess)
        monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
        out = providers.generate([{"role": "user", "content": "hi"}], CFG, retries=2)
        assert out == "Error: Z.ai API Timeout"

    def test_retries_then_gives_up(self, monkeypatch):
        sess = _FakeSession(_FakeResponse(500, text="boom"))
        monkeypatch.setattr(providers, "req", sess)
        monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
        providers.generate([{"role": "user", "content": "hi"}], CFG, retries=3)
        assert len(sess.calls) == 3


class TestZai403EndpointMismatch:
    """A Coding Plan key on the PaaS endpoint (or vice versa) returns 403 —
    the error must name both endpoints instead of retrying blindly."""

    def test_403_names_both_endpoints(self, monkeypatch):
        sess = _FakeSession(_FakeResponse(403, text="subscription quota not applicable"))
        monkeypatch.setattr(providers, "req", sess)
        out = providers.generate([{"role": "user", "content": "hi"}], {**CFG, "zai_endpoint": "paas"}, retries=3)
        assert "403" in out
        assert 'zai_endpoint="coding"' in out
        assert 'zai_endpoint="paas"' in out
        assert providers.ZAI_CODING_BASE_URL in out
        assert providers.ZAI_PAAS_BASE_URL in out
        # 403s don't heal — exactly one attempt, no retry loop
        assert len(sess.calls) == 1


class TestZaiPricing:
    def test_glm_models_priced(self):
        for m in ("glm-5.3", "glm-5-turbo", "glm-4.7", "glm-5.1"):
            assert providers._price_for(m) is not None, m

    def test_unknown_model_unpriced(self):
        # Not in MODEL_PRICING and no substring match -> _record_usage falls
        # back to DEFAULT_RATE and flags USAGE["priced"] = False.
        assert providers._price_for("totally-unknown-model") is None


class TestZaiConfig:
    """config plumbing: defaults, Pydantic validation, loader setdefault."""

    def test_redconfig_defaults_to_coding(self):
        from suijin.modules.platform.lib.config_models import RedConfig

        cfg = RedConfig(provider="zai")
        assert cfg.zai_endpoint == "coding"
        assert cfg.zai_model == "glm-5.3"

    def test_redconfig_accepts_paas_and_urls(self):
        from suijin.modules.platform.lib.config_models import RedConfig

        assert RedConfig(zai_endpoint="paas").zai_endpoint == "paas"
        assert RedConfig(zai_endpoint="PAAS").zai_endpoint == "paas"
        assert RedConfig(zai_endpoint="https://proxy.example.com/v1").zai_endpoint == "https://proxy.example.com/v1"

    def test_redconfig_rejects_unknown_endpoint(self):
        from suijin.modules.platform.lib.config_models import RedConfig

        with pytest.raises(Exception, match="zai_endpoint"):
            RedConfig(zai_endpoint="free-tier")

    def test_constants_default(self):
        from suijin.modules.platform.lib.constants import ZAI_ENDPOINT

        assert ZAI_ENDPOINT == "coding"

    def test_tui_settings_has_endpoint_choice(self):
        # Settings TUI must expose the picker so users can switch billing
        # surface without editing config.json by hand.
        from suijin.modules.console.lib.tui_settings import ALL_FIELDS

        field = ALL_FIELDS["zai_endpoint"]
        assert field[0] == "choice"
        assert field[1] == ["coding", "paas"]
        assert field[2] == ["zai"]  # only shown for the zai provider

    def test_doctor_shows_zai_endpoint(self, monkeypatch, tmp_path, capsys):
        import json as _json

        from suijin.modules.console.lib import cli

        cfg = tmp_path / "config.json"
        cfg.write_text(_json.dumps({"provider": "zai", "zai_endpoint": "paas"}))
        monkeypatch.setattr(cli, "_PKG_DIR", str(tmp_path))
        monkeypatch.setattr(cli, "_has_any_api_key", lambda _p: True)
        # Neutralize environment-dependent checks so run_doctor completes.
        monkeypatch.setattr(cli.shutil, "which", lambda _b: "/bin/true")
        monkeypatch.setattr(cli, "REQUIRED_BINARIES", [])
        monkeypatch.setattr(cli, "_importable", lambda _m: True)
        monkeypatch.setattr(cli, "_port_free", lambda _p: True)
        code = cli.run_doctor()
        out = capsys.readouterr().out
        assert code == 0
        assert "provider=zai" in out
        assert "endpoint=paas" in out
        assert "pay-as-you-go" in out


class TestActiveModelResolution:
    """The status line / display model must be provider-aware.

    Regression: selecting zai displayed 'zai / deepseek-ai/DeepSeek-V4-Flash'
    because final_model_id (an HF-style id) was the hardcoded cross-provider
    default — even while the actual API call correctly used glm-5.3.
    """

    DEFAULT_CFG = {
        "final_model_id": "deepseek-ai/DeepSeek-V4-Flash",  # written by default config
        "zai_model": "glm-5.3",
        "deepseek_model": "deepseek-v4-flash",
        "gemini_model": "gemini-2.5-flash",
        "anthropic_model": "claude-opus-4-7",
    }

    def test_zai_shows_glm_not_final_model_id(self):
        from suijin.modules.redteam.lib.red.config_loader import active_model

        cfg = {**self.DEFAULT_CFG, "provider": "zai"}
        assert active_model(cfg) == "glm-5.3"

    def test_every_provider_resolves_its_own_model(self):
        from suijin.modules.redteam.lib.red.config_loader import active_model

        for provider, expected in (
            ("zai", "glm-5.3"),
            ("deepseek", "deepseek-v4-flash"),
            ("gemini", "gemini-2.5-flash"),
            ("anthropic", "claude-opus-4-7"),
        ):
            assert active_model({**self.DEFAULT_CFG, "provider": provider}) == expected

    def test_huggingface_keeps_final_model_id(self):
        from suijin.modules.redteam.lib.red.config_loader import active_model

        assert active_model({**self.DEFAULT_CFG, "provider": "huggingface"}) == "deepseek-ai/DeepSeek-V4-Flash"

    def test_zai_endpoint_variant_shows_model(self):
        from suijin.modules.redteam.lib.red.config_loader import active_model

        cfg = {**self.DEFAULT_CFG, "provider": "zai", "zai_model": "glm-5-turbo", "zai_endpoint": "paas"}
        assert active_model(cfg) == "glm-5-turbo"

    def test_missing_everything_is_auto(self):
        from suijin.modules.redteam.lib.red.config_loader import active_model

        assert active_model({}) == "auto"
        assert active_model(None) == "auto"

    def test_llm_client_uses_active_model(self, monkeypatch):
        # v5.2: the Thinking... spinner is GONE (it fought the engagement
        # strip's Live region — violent flashing in live runs). The wrapper
        # is silent; this now asserts the no-spinner contract.
        import asyncio

        from suijin.modules.redteam.lib.red import llm_client

        monkeypatch.setattr(llm_client, "_generate", lambda msgs, cfg: "ok")
        out = asyncio.run(
            llm_client.generate_async([{"role": "user", "content": "hi"}], {**self.DEFAULT_CFG, "provider": "zai"})
        )
        assert out == "ok"
        assert not hasattr(llm_client, "console")  # no second console, no status Live


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class TestDeepSeekTimeoutRegression:
    """DeepSeek's retry loop used to fall through to 'Unknown provider'
    instead of returning a timeout message (fixed alongside the Z.ai work)."""

    def test_timeout_returns_deepseek_message(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        sess = _FakeSession(_FakeResponse(500, text="boom"))
        monkeypatch.setattr(providers, "req", sess)
        monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
        cfg = {
            "provider": "deepseek",
            "deepseek_model": "deepseek-v4-flash",
            "temperature": 0.4,
            "max_tokens_per_request": 8000,
        }
        out = providers.generate([{"role": "user", "content": "hi"}], cfg, retries=2)
        assert out == "Error: DeepSeek API Timeout"
