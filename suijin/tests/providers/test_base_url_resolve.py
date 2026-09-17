"""Tests for local provider base URL resolution.

Cover the env override, the Docker localhost rewrite, and the no op cases.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.providers.lib import _LOCAL_HOST_ENVS, _resolve_base_url  # noqa: E402
from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY  # noqa: E402


def _clear_local_env(monkeypatch):
    """Clear the Docker flag and every local host override."""
    monkeypatch.delenv("SUIJIN_DOCKER", raising=False)
    for env in _LOCAL_HOST_ENVS.values():
        monkeypatch.delenv(env, raising=False)


def test_no_env_returns_registry_value(monkeypatch):
    _clear_local_env(monkeypatch)
    spec = PROVIDER_REGISTRY["ollama"]
    assert _resolve_base_url(spec) == spec.base_url


def test_env_override_bare_host_keeps_path(monkeypatch):
    _clear_local_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "gpu.lan:1234")
    spec = PROVIDER_REGISTRY["ollama"]
    assert _resolve_base_url(spec) == "http://gpu.lan:1234/v1"


def test_env_override_full_url_without_path_keeps_path(monkeypatch):
    _clear_local_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu.lan:9999")
    spec = PROVIDER_REGISTRY["ollama"]
    assert _resolve_base_url(spec) == "http://gpu.lan:9999/v1"


def test_env_override_full_url_with_path_is_kept(monkeypatch):
    _clear_local_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu.lan:9999/custom")
    spec = PROVIDER_REGISTRY["ollama"]
    assert _resolve_base_url(spec) == "http://gpu.lan:9999/custom"


def test_docker_rewrites_localhost(monkeypatch):
    _clear_local_env(monkeypatch)
    monkeypatch.setenv("SUIJIN_DOCKER", "1")
    spec = PROVIDER_REGISTRY["ollama"]
    assert _resolve_base_url(spec) == "http://host.docker.internal:11434/v1"


def test_docker_flag_off_leaves_localhost(monkeypatch):
    _clear_local_env(monkeypatch)
    spec = PROVIDER_REGISTRY["ollama"]
    assert "localhost" in _resolve_base_url(spec)


def test_cloud_spec_never_rewritten_in_docker(monkeypatch):
    _clear_local_env(monkeypatch)
    monkeypatch.setenv("SUIJIN_DOCKER", "1")
    spec = PROVIDER_REGISTRY["openai"]
    assert _resolve_base_url(spec) == spec.base_url


def test_env_override_wins_over_docker(monkeypatch):
    _clear_local_env(monkeypatch)
    monkeypatch.setenv("SUIJIN_DOCKER", "1")
    monkeypatch.setenv("OLLAMA_HOST", "gpu.lan:1234")
    spec = PROVIDER_REGISTRY["ollama"]
    assert _resolve_base_url(spec) == "http://gpu.lan:1234/v1"
