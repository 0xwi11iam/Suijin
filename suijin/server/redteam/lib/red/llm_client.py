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


def _resolve_timeout(config: dict) -> float:
    """The hard call timeout: `llm_timeout` (seconds) from config wins;
    default 180s, 300s for local providers — laptop-CPU prompt prefill
    on larger contexts takes >3 minutes and the old hardcoded cutoff
    killed the call before generation started (field report 2026-09-30:
    Ollama loops + 180s timeouts)."""
    prov = str((config or {}).get("provider") or "").lower()
    default_to = 300.0 if prov == "ollama" else 180.0
    try:
        return float((config or {}).get("llm_timeout") or 0) or default_to
    except (TypeError, ValueError):
        return default_to


async def generate_async(messages, config=None, on_delta=None):
    """Async LLM call. Silent — the engagement strip in red/console_ui.py
    owns all live display; `on_delta(kind, text)` streams tokens to it.

    Hard timeout via _resolve_timeout (config `llm_timeout`; 180s default,
    300s local). 600s was an invisible 10-minute stall when the provider
    hung (the operator saw "it stopped"); the defaults stay well under
    that."""
    if not config:
        config = load_config()
    elif not (config.get("provider") or "").strip():
        # PARTIAL config (e.g. {"intelligence": "max"} from the stream
        # wrapper) is truthy — the old `if not config` rescue never fired
        # and generate() silently defaulted to deepseek while the
        # operator's config said zai. Merge the real config underneath.
        merged = dict(load_config())
        merged.update(config)
        config = merged

    _to = _resolve_timeout(config)

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_generate, messages, config, on_delta),
            timeout=_to,
        )
    except asyncio.TimeoutError:
        return (
            f"Error: LLM request timed out after {int(_to)}s (no transport progress). "
            "The provider may be stuck — retry or switch providers."
        )


def _generate(messages, config, on_delta=None):
    """Thread-friendly wrapper that lazily resolves the providers module."""
    from suijin.modules.loader import load_local_module

    providers = load_local_module("providers")
    fn = getattr(providers, "generate_with_failover", None)
    if fn and (config or {}).get("fallback_providers"):
        return fn(messages, config, on_delta=on_delta)
    if fn:
        # SELF-HEALING: no configured chain → auto-chain from the registry:
        # the first key-bearing cloud provider + local (ollama). A credit
        # death on the primary falls through instead of killing the run.
        chain = _auto_chain(config)
        if chain:
            cfg = dict(config or {})
            cfg["fallback_providers"] = chain
            return fn(messages, cfg, on_delta=on_delta)
    return providers.generate(messages, config, on_delta=on_delta)


def _auto_chain(config) -> list[str]:
    """[first key-bearing registry cloud provider, local] — bounded to
    two so a fully-dead night costs seconds, not minutes, of backoff.

    ollama is appended ONLY when the operator actually configured it
    (an `ollama_model` in config). The unconditional "keyless local,
    always free" append cost a whole engagement on 2026-09-30: the
    primary's key was missing from a stale process env, no cloud key
    was visible either, so the chain was [ollama] alone — three 6s
    connection timeouts per call and a wall of "Ollama unreachable"
    masking the real error ("Z.ai key not set")."""
    try:
        from suijin.modules.providers.lib import env_key
        from suijin.modules.providers.lib.registry import CLOUD_KEYS, PROVIDER_REGISTRY

        primary = str((config or {}).get("provider") or "").lower()
        chain: list[str] = []
        for key in CLOUD_KEYS:
            if key == primary:
                continue
            spec = PROVIDER_REGISTRY.get(key)
            if spec and spec.key_envs and any(env_key(e) for e in spec.key_envs):
                chain.append(key)
                break
        if (config or {}).get("ollama_model"):
            chain.append("ollama")  # configured local — the operator asked for it
        return [p for p in chain if p != primary]
    except Exception:  # noqa: BLE001 — self-healing must never break a call
        return []
