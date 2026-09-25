"""Every provider we ADVERTISE must actually work end to end.

The registry is a table of promises: each row claims a provider works.
This walks ALL of them — registry rows, the bespoke code-path providers
and local boxes — and checks the whole chain a person depends on:

  selectable in Settings  → provider_choices()
  key resolvable          → provider_key_env()   (dynamic, never hardcoded)
  endpoint resolvable     → provider_models_endpoint() (or an honest reason)
  model field + row       → the editor synthesizes <provider>_model
  a key can be stored     → set_provider_key() writes .env, not config.json

A provider that is listed but not editable (or not keyable) is a broken
promise, so every one of these is a test, not a comment.
"""

from __future__ import annotations

import json

import pytest

from suijin.modules.console.lib import settings_tui as st
from suijin.modules.providers.lib import (
    provider_key_env,
    provider_models_endpoint,
    set_provider_key,
)
from suijin.modules.providers.lib.registry import CLOUD_KEYS, LOCAL_KEYS, PROVIDER_REGISTRY

#: the hand-written code-path providers (their env names live in the
#: provider layer beside the code that reads them)
BESPOKE = ("zai", "deepseek", "gemini", "anthropic", "huggingface", "amd")

ALL_KEYS = sorted(set(PROVIDER_REGISTRY) | set(BESPOKE))


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """A throwaway config so the editor reads a known provider."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"provider": "zai"}), encoding="utf-8")
    monkeypatch.setattr(st, "CONFIG_PATH", str(p))
    return p


class TestEveryProviderIsUsable:
    def test_registry_is_not_empty(self):
        assert len(PROVIDER_REGISTRY) >= 40, "the registry shrank — did a migration drop rows?"

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_selectable_in_settings(self, key, cfg_file):
        assert key in st.provider_choices(), f"{key} is advertised but not selectable"

    @pytest.mark.parametrize("key", sorted(k for k, s in PROVIDER_REGISTRY.items() if s.requires_key))
    def test_key_env_is_resolved(self, key):
        assert provider_key_env(key), f"{key} needs a key but none resolves"

    @pytest.mark.parametrize("key", sorted(PROVIDER_REGISTRY))
    def test_endpoint_or_an_honest_reason(self, key):
        spec = PROVIDER_REGISTRY[key]
        base, reason = provider_models_endpoint(key, {})
        if spec.local:
            assert not base and "local" in str(reason), f"{key} is local but claims a cloud endpoint"
        else:
            assert base, f"{key} has no endpoint: {reason}"
            assert str(base).startswith("http"), f"{key} endpoint is not a URL: {base}"

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_editor_has_a_model_field(self, key, cfg_file):
        visible = st._visible_fields({"provider": key})
        assert any(f[0] == "model" for _k, f in visible.items()), f"{key}: no model field in the editor"

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_model_row_is_rendered(self, key, cfg_file):
        config = {"provider": key}
        visible = st._visible_fields(config)
        mkey = f"{key}_model"
        if mkey not in visible:
            pytest.skip(f"{key} shares a declared model field (bespoke)")
        rows = [t for t, _role in st.screen_lines(config, cursor=0)]
        assert any(mkey in t for t in rows), f"{key}: the model row is missing from the screen"
        # and the cursor can actually land on it
        items = [k for _g, k in st._row_items(visible)]
        assert mkey in items, f"{key}: the model row is not reachable by the cursor"

    def test_bespoke_providers_resolve(self):
        """The hand-written paths resolve their env name and either an
        endpoint or an honest 'it uses its own SDK' reason."""
        for key in BESPOKE:
            assert provider_key_env(key), f"{key}: bespoke provider with no env name"
            base, reason = provider_models_endpoint(key, {})
            assert base or "SDK" in str(reason), f"{key}: no endpoint and no reason"

    def test_local_providers_need_no_key(self):
        for key in LOCAL_KEYS:
            assert provider_key_env(key) == "", f"{key} is local but wants a key"
            assert not PROVIDER_REGISTRY[key].key_envs, f"{key} is local but declares a key env"

    def test_custom_provider_uses_its_config(self):
        config = {"custom_providers": [{"name": "lan", "base_url": "http://10.0.0.9:1234/v1", "api_key": "k"}]}
        base, headers = provider_models_endpoint("custom:lan", config)
        assert base == "http://10.0.0.9:1234/v1"
        assert headers["Authorization"] == "Bearer k"

    def test_cloud_keys_are_displayed(self):
        """Anything a person can pick from the cloud list resolves a key."""
        for key in CLOUD_KEYS:
            assert key in PROVIDER_REGISTRY, f"{key} is in CLOUD_KEYS but has no spec"
            assert PROVIDER_REGISTRY[key].key_envs, f"{key} is cloud but has no key env"


class TestStoringKeys:
    """A key goes to .env — never to config.json, which is snapshotted
    into bundles and read by other tools."""

    def test_writes_env_not_config(self, monkeypatch, tmp_path):
        from suijin.modules.platform.lib import config_loader as cl

        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=1\n", encoding="utf-8")
        monkeypatch.setattr(cl, "ENV_PATH", env_path)
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")  # restored at teardown
        ok, message = set_provider_key("opencode", "sk-test")
        assert ok, message
        assert "OPENCODE_API_KEY=sk-test" in env_path.read_text()
        assert "EXISTING=1" in env_path.read_text()  # other keys untouched

    def test_keyless_provider_is_refused(self):
        ok, message = set_provider_key("ollama", "x")
        assert not ok and "no API key" in message

    def test_empty_key_is_refused(self, monkeypatch, tmp_path):
        from suijin.modules.platform.lib import config_loader as cl

        env_path = tmp_path / ".env"
        env_path.write_text("", encoding="utf-8")
        monkeypatch.setattr(cl, "ENV_PATH", env_path)
        ok, _ = set_provider_key("opencode", "   ")
        assert not ok
        assert env_path.read_text() == ""  # nothing written
