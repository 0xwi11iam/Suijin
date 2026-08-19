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


def record(state: dict) -> dict:
    """Snapshot the profile into state (called from think_node)."""
    prof = profile_messages(state.get("messages") or [])
    trend = list(state.get("_prompt_profile_trend") or [])
    trend.append(prof["est_tokens"])
    state["_prompt_profile"] = prof
    state["_prompt_profile_trend"] = trend[-50:]  # bounded memory
    return prof


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
