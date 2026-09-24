"""Prompt budget profiler (D31) — where the tokens go, per iteration.

Estimates (chars/4, the standard rough ratio) the size of each message
segment the LLM sees: system prompt, conversation history, tool
results. think_node snapshots the profile each iteration into
state["_prompt_profile"] so the final state (and the saved session)
carries the trend; the CLI renders it.
"""

from __future__ import annotations


def _chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    try:
        import json

        return len(json.dumps(content))
    except (TypeError, ValueError):
        return len(str(content))


def profile_messages(messages: list) -> dict:
    """Token estimate breakdown for one messages list."""
    system = history = 0
    for m in messages or []:
        n = _chars(m.get("content", ""))
        if m.get("role") == "system":
            system += n
        else:
            history += n
    est_tokens = (system + history) // 4
    return {
        "system_chars": system,
        "history_chars": history,
        "est_tokens": est_tokens,
        "messages": len(messages or []),
    }


def record(state: dict, wire_messages: list | None = None) -> dict:
    """Snapshot the profile into state (called from think_node).

    `wire_messages` = the ACTUAL messages array sent to the provider
    (system + context + user turns). Without it we fall back to the
    state history — which understates every turn by the full static
    prompt (tool catalog + doctrine), the old bug."""
    prof = profile_messages(wire_messages if wire_messages is not None else state.get("messages") or [])
    prof["window_tokens"] = _window_tokens(state)
    if prof["window_tokens"]:
        prof["window_pct"] = round(100 * prof["est_tokens"] / prof["window_tokens"], 1)
    trend = list(state.get("_prompt_profile_trend") or [])
    trend.append(prof["est_tokens"])
    state["_prompt_profile"] = prof
    state["_prompt_profile_trend"] = trend[-50:]  # bounded memory
    return prof


def _window_tokens(state: dict) -> int:
    """Resolve the model's context window for the pct gauge; 0 unknown."""
    try:
        cfg = state.get("_run_config") or {}
        prov = str(cfg.get("provider") or "")
        model = str(cfg.get(f"{prov}_model") or "") if prov else ""
        from suijin.modules.providers.lib.model_meta import resolve_context_window

        return resolve_context_window(prov, model, cfg)
    except Exception:  # noqa: BLE001
        return 0


def render(state: dict) -> str:
    """Operator-facing summary from a final state / session dict."""
    prof = state.get("_prompt_profile")
    if not prof:
        return "No prompt profile recorded for this session."
    trend = state.get("_prompt_profile_trend") or []
    lines = [
        f"last iteration : ~{prof['est_tokens']:,} est tokens across {prof['messages']} messages",
        f"  system prompt: {prof['system_chars']:,} chars (~{prof['system_chars'] // 4:,} tok)",
        f"  history      : {prof['history_chars']:,} chars (~{prof['history_chars'] // 4:,} tok)",
    ]
    if prof.get("window_tokens"):
        lines.append(f"  context use  : {prof.get('window_pct', 0)}% of the {prof['window_tokens']:,}-token window")
        if float(prof.get("window_pct", 0)) > 80:
            lines.append("  WARNING: over 80% of the context window — compaction/limits should be tighter")
    if len(trend) > 1:
        growth = trend[-1] - trend[0]
        per_iter = growth / (len(trend) - 1)
        lines.append(
            f"growth         : {trend[0]:,} -> {trend[-1]:,} tok over {len(trend)} iterations (~{per_iter:+,.0f}/iter)"
        )
        if per_iter > 2000:
            lines.append(
                "  high per-iteration growth — long engagements will hit context limits; consider trimming tool outputs"
            )
    return "\n".join(lines)
