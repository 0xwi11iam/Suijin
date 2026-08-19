"""
suijin/core/red/llm_client.py — Async LLM wrapper with status display + timeout.

Extracted from redteamer.py. Wraps provider.generate() with a Rich status
spinner and a 90s hard timeout so slow providers never hang the TUI.
"""

from __future__ import annotations

import asyncio

from rich.console import Console

from suijin.modules.redteam.lib.red.config_loader import active_model, load_config

console = Console()


async def generate_async(messages, config=None):
    """Async LLM call with live status display and hard timeout."""
    if not config:
        config = load_config()
    provider = config.get("provider", "unknown")
    model = active_model(config)
    msg_count = len(messages)
    prompt_chars = sum(len(m.get("content", "")) for m in messages)

    # Limit to 90s total — prevents UI hangs from slow API/network
    try:
        with console.status(
            f"[bold cyan]Thinking... ({provider}/{model}) — {msg_count} msgs, {prompt_chars // 1000}k chars[/bold cyan]",
            spinner="dots",
        ):
            result = await asyncio.wait_for(
                asyncio.to_thread(_generate, messages, config),
                timeout=90.0,
            )
    except asyncio.TimeoutError:
        result = "Error: LLM request timed out after 90s. The provider may be overloaded. Retry with a shorter prompt or switch providers."
        console.print("[yellow]  (LLM timed out after 90s)[/yellow]")

    return result


def _generate(messages, config):
    """Thread-friendly wrapper that lazily resolves the providers module."""
    from suijin.modules.loader import load_local_module

    providers = load_local_module("providers")
    fn = getattr(providers, "generate_with_failover", None)
    # honor config['fallback_providers'] when configured; plain generate otherwise
    if fn and (config or {}).get("fallback_providers"):
        return fn(messages, config)
    return providers.generate(messages, config)
