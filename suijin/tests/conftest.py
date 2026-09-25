"""Pytest configuration — shared fixtures and mocks for Suijin tests."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on path (3 levels up: tests -> suijin -> repo root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ── HERMETIC SANDBOX ───────────────────────────────────────────────────────
# Everything below runs at conftest IMPORT time, which is before pytest
# imports a single test module — and that ordering is load-bearing.
# Path.home() and the workspace/config/env paths are baked into module
# constants the moment suijin is first imported (loader.PACK_ROOTS,
# workspace.WORKSPACE_DIR, config_loader.CONFIG_PATH/ENV_PATH), so the
# redirects have to be in place BEFORE that happens or they do nothing.
#
# Without this the suite ran against the developer's real machine: it
# loaded their installed packs from ~/.suijin/modules, read their
# config.json and .env, and could write into their live workspace. That
# made results depend on the machine, which is how an order-dependent
# flake in the red-team console tests survived for so long.
_SANDBOX = Path(tempfile.mkdtemp(prefix="suijin-tests-"))
(_SANDBOX / "home").mkdir()
(_SANDBOX / "workspace").mkdir()
(_SANDBOX / "home" / ".suijin" / "modules").mkdir(parents=True)

_SANDBOX_CONFIG = _SANDBOX / "config.json"
# Deliberately minimal and neutral: the operator's real settings (their
# pinned provider, model and caps) must never influence a test result.
_SANDBOX_CONFIG.write_text(json.dumps({"provider": "deepseek"}), encoding="utf-8")
_SANDBOX_ENV = _SANDBOX / "sandbox.env"
_SANDBOX_ENV.write_text("", encoding="utf-8")

os.environ["HOME"] = str(_SANDBOX / "home")
os.environ["USERPROFILE"] = os.environ["HOME"]  # windows Path.home()
os.environ["SUIJIN_WORKSPACE"] = str(_SANDBOX / "workspace")
os.environ["SUIJIN_CONFIG"] = str(_SANDBOX_CONFIG)
os.environ["SUIJIN_ENV"] = str(_SANDBOX_ENV)

# The empty sandbox .env already means load_env() injects nothing; this is
# belt-and-braces for keys inherited from the developer's own shell. The
# list is derived from the provider registry inside _no_live_keys() below,
# because a hand-kept list is exactly what missed OPENCODE_API_KEY before.
for _leak in ("OPENCODE_API_KEY", "ZAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_leak, None)

# Don't leave a sandbox behind in the temp dir on every run.
import atexit  # noqa: E402
import shutil  # noqa: E402

atexit.register(lambda: shutil.rmtree(_SANDBOX, ignore_errors=True))

# Stealth pacing off in tests: burst-limiter sleeps are real time; the
# logic itself is unit-tested with an injected clock.
os.environ.setdefault("SUIJIN_STEALTH_PACING", "0")


@pytest.fixture(scope="session", autouse=True)
def _no_live_keys():
    """Strip every provider key the environment happens to carry.

    Runs as a fixture rather than at import so the registry can be
    imported first (the sandbox paths are already set by then, which is
    what makes importing safe). Deriving the list means a newly added
    provider is covered automatically instead of being forgotten.
    """
    import contextlib

    names: set[str] = set()
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

        for spec in PROVIDER_REGISTRY.values():
            names.update(spec.key_envs)
    for name in names:
        os.environ.pop(name, None)
    yield


@pytest.fixture(scope="session", autouse=True)
def _runtime_once():
    """The test suite is an entry point: initialize the runtime explicitly
    (Phase 0 contract — importing platform runtime no longer discovers module
    packs / migrates the workspace as an import side effect)."""
    from suijin.modules.platform.lib.runtime import init_runtime

    init_runtime()


@pytest.fixture(autouse=True)
def reset_cost_tracking():
    """Reset provider cost tracking before each test."""
    try:
        from suijin.modules.providers.lib import reset_usage

        reset_usage()
    except ImportError:
        pass


@pytest.fixture
def mock_provider(monkeypatch):
    """Mock the LLM provider to avoid real API calls."""

    def mock_generate(messages, config=None, **kwargs):
        return '{"verdict":"FLAGGED","score":8,"action":"DECEIVE","reasoning":"Test response"}'

    monkeypatch.setattr("suijin.modules.providers.lib.generate", mock_generate)
    return mock_generate


@pytest.fixture
def sample_http_request():
    """Sample request dict matching the traffic log format."""
    return {
        "method": "POST",
        "path": "/auth/login",
        "ip": "127.0.0.1",
        "body": '{"username":"admin\' OR \'1\'=\'1","password":"x"}',
        "user_agent": "curl/8.7.1",
        "query": {},
        "headers": {"Content-Type": "application/json"},
    }
