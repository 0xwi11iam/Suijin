"""
suijin/core/red/llm_client.py — Async LLM wrapper with hard timeout.

Extracted from redteamer.py. Wraps provider.generate() with a 90s hard
timeout so slow providers never hang the TUI.

v5.2: the Rich status spinner is GONE. It was a second Live region running
concurrently with the engagement strip's Live on the same terminal — the
two fought over the cursor and the whole screen flashed violently during
live runs. The engagement strip (spinner + tokens/cost) is the one and
only live region; this wrapper is now silent.
"""

from __future__ import annotations

import asyncio

from suijin.modules.redteam.lib.red.config_loader import load_config


async def generate_async(messages, config=None, on_delta=None):
    """Async LLM call. Silent — the engagement strip in red/console_ui.py
    owns all live display; `on_delta(kind, text)` streams tokens to it.

    Hard timeout is 180s: 600s was an invisible 10-minute stall when
    the provider hung (the operator saw "it stopped"); 180s covers the
    biggest legitimate completions with retries."""
    if not config:
        config = load_config()

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_generate, messages, config, on_delta),
            timeout=180.0,
        )
    except asyncio.TimeoutError:
        return (
            "Error: LLM request timed out after 180s (no transport progress). "
            "The provider may be stuck — retry or switch providers."
        )


def _generate(messages, config, on_delta=None):
    """Thread-friendly wrapper that lazily resolves the providers module."""
    from suijin.modules.loader import load_local_module

    providers = load_local_module("providers")
    fn = getattr(providers, "generate_with_failover", None)
    # honor config['fallback_providers'] when configured; plain generate otherwise
    if fn and (config or {}).get("fallback_providers"):
        return fn(messages, config, on_delta=on_delta)
    return providers.generate(messages, config, on_delta=on_delta)
