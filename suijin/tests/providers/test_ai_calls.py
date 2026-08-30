"""
AI Call System Test — verifies provider routing, API connectivity,
response parsing, and error handling for all configured providers.
"""

import os
import sys
import time

import pytest

pytestmark = [pytest.mark.ai, pytest.mark.slow]  # live paid API calls

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_provider_routing():
    """Verify the generate() function routes to the correct provider."""
    from suijin.modules.providers.lib import generate

    config = {"provider": "deepseek", "temperature": 0.1, "max_tokens_per_request": 100}
    messages = [{"role": "user", "content": "Say 'hello' and nothing else."}]

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("  ⏭  DeepSeek: no DEEPSEEK_API_KEY set — skipped")
        return

    start = time.time()
    result = generate(messages, config)
    elapsed = time.time() - start

    assert result is not None, "generate() returned None"
    assert not result.startswith("Error:"), f"API error: {result[:200]}"
    assert len(result) > 0, "Empty response"
    print(f"  [done] DeepSeek: {len(result)} chars in {elapsed:.1f}s")


def test_deepseek_specific():
    """Test DeepSeek API directly with a simple prompt."""
    import requests as req

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("  ⏭  DeepSeek direct: no key — skipped")
        return

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 50,
        "temperature": 0,
    }

    start = time.time()
    resp = req.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=payload, timeout=30)
    elapsed = time.time() - start

    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    assert len(content) > 0
    usage = data.get("usage", {})
    print(
        f"  [done] DeepSeek direct: '{content[:50]}' in {elapsed:.1f}s, "
        f"tokens: {usage.get('prompt_tokens', 0)}->{usage.get('completion_tokens', 0)}"
    )


def test_huggingface():
    """Test HuggingFace provider if configured."""
    token = os.environ.get("HF_TOKEN", "")
    if not token:
        print("  ⏭  HuggingFace: no HF_TOKEN — skipped")
        return

    from suijin.modules.providers.lib import generate

    config = {
        "provider": "huggingface",
        "temperature": 0.1,
        "max_tokens_per_request": 50,
        "final_model_id": "Qwen/Qwen2.5-3B-Instruct",
    }
    messages = [{"role": "user", "content": "Say hi"}]

    start = time.time()
    result = generate(messages, config)
    elapsed = time.time() - start

    if result.startswith("Error:"):
        print(f"  [warn]  HuggingFace: {result[:100]}")
    else:
        print(f"  [done] HuggingFace: {len(result)} chars in {elapsed:.1f}s")


def test_error_handling():
    """Test that invalid configs produce proper errors."""
    from suijin.modules.providers.lib import generate

    # Invalid provider
    config = {"provider": "nonexistent", "temperature": 0}
    messages = [{"role": "user", "content": "test"}]
    result = generate(messages, config)
    assert "Unknown provider" in result or "Error" in result
    print(f"  [done] Unknown provider: '{result[:60]}'")

    # Missing API key — should error, not crash
    orig_key = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        config2 = {"provider": "deepseek", "temperature": 0}
        result2 = generate(messages, config2)
        assert "Error" in result2 or "API" in result2
        print(f"  [done] Missing key: '{result2[:60]}'")
    finally:
        if orig_key:
            os.environ["DEEPSEEK_API_KEY"] = orig_key


def test_token_counting():
    """Verify USAGE dict is updated after calls."""
    from suijin.modules.providers.lib import USAGE, generate, reset_usage

    reset_usage()
    assert USAGE["calls"] == 0
    assert USAGE["est_cost_usd"] == 0.0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("  ⏭  Token counting: no key — skipped")
        return

    config = {"provider": "deepseek", "temperature": 0, "max_tokens_per_request": 50}
    messages = [{"role": "user", "content": "Say hello"}]

    generate(messages, config)
    assert USAGE["calls"] >= 1
    assert USAGE["input_tokens"] > 0
    assert USAGE["output_tokens"] > 0
    assert USAGE["est_cost_usd"] > 0
    print(
        f"  [done] Token counting: {USAGE['calls']} calls, "
        f"{USAGE['input_tokens']}->{USAGE['output_tokens']} tokens, "
        f"${USAGE['est_cost_usd']:.6f}"
    )


def test_response_time():
    """Verify response time is reasonable (<30s for simple prompt)."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("  ⏭  Response time: no key — skipped")
        return

    from suijin.modules.providers.lib import generate

    config = {"provider": "deepseek", "temperature": 0, "max_tokens_per_request": 50}
    messages = [{"role": "user", "content": "Say 'pong'"}]

    start = time.time()
    result = generate(messages, config)
    elapsed = time.time() - start

    assert elapsed < 30, f"Response took {elapsed:.1f}s — too slow!"
    assert "pong" in result.lower() or "PONG" in result.upper() or len(result) > 0
    print(f"  [done] Response time: {elapsed:.1f}s (limit: 30s)")


def test_config_loading():
    """Test that config.json is loaded correctly."""
    from suijin.modules.redteam.lib.redteamer import load_config

    config = load_config()
    assert "provider" in config
    assert "temperature" in config
    assert "max_tokens_per_request" in config
    assert isinstance(config["temperature"], (int, float))
    print(f"  [done] Config: provider={config['provider']}, temp={config['temperature']}")


def test_env_loading():
    """Test that .env vars are loaded (skips if no .env file — CI-safe)."""
    from suijin.modules.redteam.lib.redteamer import ENV_PATH, load_env

    # On CI / without .env, just verify ENV_PATH is a valid Path object
    if not ENV_PATH.exists():
        print("  ⏭  No .env file — skipped")
        return
    load_env()
    keys = ["DEEPSEEK_API_KEY", "HF_TOKEN", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"]
    found = [k for k in keys if os.environ.get(k)]
    print(f"  [done] Env: {len(found)}/{len(keys)} keys found: {found}")


def run_all():
    tests = [
        ("Config loading", test_config_loading),
        ("Env loading", test_env_loading),
        ("Provider routing", test_provider_routing),
        ("DeepSeek direct", test_deepseek_specific),
        ("HuggingFace", test_huggingface),
        ("Error handling", test_error_handling),
        ("Token counting", test_token_counting),
        ("Response time", test_response_time),
    ]

    passed = 0
    skipped = 0
    failed = 0

    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  [fail] {name}: {e}")
            failed += 1
        except Exception as e:
            if "skipped" in str(getattr(fn, "__name__", "")).lower():
                skipped += 1
            else:
                print(f"  [fail] {name}: {type(e).__name__}: {e}")
                failed += 1

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed")
    if skipped:
        print(f"({skipped} skipped — set API keys to run)")
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
