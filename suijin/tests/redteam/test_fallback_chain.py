"""Tests for the effective fallback chain.

The banner and _generate must agree on the chain, so the resolver is
shared. These tests lock the resolver behaviour.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.providers.lib.registry import CLOUD_KEYS, PROVIDER_REGISTRY  # noqa: E402
from suijin.modules.redteam.lib.red.llm_client import effective_fallback_chain  # noqa: E402


def _clear_cloud_keys(monkeypatch):
    """Clear every cloud provider key so the auto chain is deterministic."""
    for key in CLOUD_KEYS:
        spec = PROVIDER_REGISTRY.get(key)
        if not spec:
            continue
        for env in spec.key_envs:
            monkeypatch.delenv(env, raising=False)


def test_configured_chain_wins(monkeypatch):
    _clear_cloud_keys(monkeypatch)
    config = {"provider": "deepseek", "fallback_providers": ["a", "b"]}
    assert effective_fallback_chain(config) == ["a", "b"]


def test_no_keys_falls_back_to_ollama(monkeypatch):
    _clear_cloud_keys(monkeypatch)
    assert effective_fallback_chain({"provider": "zai"}) == ["ollama"]


def test_cloud_key_precedes_ollama(monkeypatch):
    _clear_cloud_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert effective_fallback_chain({"provider": "zai"}) == ["openai", "ollama"]


def test_primary_is_removed_from_chain(monkeypatch):
    _clear_cloud_keys(monkeypatch)
    assert effective_fallback_chain({"provider": "ollama"}) == []
