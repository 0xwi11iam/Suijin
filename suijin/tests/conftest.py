"""Pytest configuration — shared fixtures and mocks for Suijin tests."""

import os
import sys

import pytest

# Ensure project root is on path (3 levels up: tests -> suijin -> repo root)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Stealth pacing off in tests: burst-limiter sleeps are real time; the
# logic itself is unit-tested with an injected clock.
os.environ.setdefault("SUIJIN_STEALTH_PACING", "0")


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
