"""Context compaction (A7) — survive long engagements.

When conversation history grows past a share of the token budget, older
tool-result messages are compressed into a single summary message (what
was tried, what worked, current facts). The system prompt and the most
recent K exchanges are preserved verbatim.
"""

from __future__ import annotations

DEFAULT_TRIGGER_CHARS = 120_000  # ~30k tokens of history
KEEP_RECENT = 8  # messages kept verbatim at the tail


def _chars(msg) -> int:
    return len(str(msg.get("content", "")))


def history_chars(messages: list) -> int:
    return sum(_chars(m) for m in messages or [] if m.get("role") != "system")


def needs_compaction(messages: list, trigger_chars: int = DEFAULT_TRIGGER_CHARS) -> bool:
    return history_chars(messages) >= trigger_chars


def _summarize_older(messages: list) -> str:
    """Deterministic digest of the messages being compressed (no LLM —
    compaction must work when the provider is the scarce resource)."""
    tools_ok, tools_fail, notes = [], [], []
    for m in messages:
        c = str(m.get("content", ""))
        role = m.get("role")
        if role == "user" and c.startswith("RESULT ("):
            # header like: RESULT (nmap_scan, 840ms, iteration 3):
            head = c.splitlines()
            first = head[0][:80] if head else ""
            ok_step = "Error" not in c[:200] and "FAIL" not in first
            (tools_ok if ok_step else tools_fail).append(first)
        elif role == "user" and "NOTE:" in c[:12]:
            notes.append(c[:120])
    lines = ["[CONTEXT COMPACTED — digest of earlier steps]"]
    if tools_ok:
        lines.append(f"successful results ({len(tools_ok)}): " + "; ".join(tools_ok[-10:]))
    if tools_fail:
        lines.append(f"failed results ({len(tools_fail)}, do not repeat blindly): " + "; ".join(tools_fail[-6:]))
    if notes:
        lines.append("notes: " + "; ".join(notes[-5:]))
    lines.append("If you need an older result's details, re-run that tool.")
    return "\n".join(lines)


def compact(messages: list, trigger_chars: int = DEFAULT_TRIGGER_CHARS, keep_recent: int = KEEP_RECENT) -> list:
    """Return a new message list, compacted when over budget. Never
    mutates the input; no-op under the trigger."""
    if not needs_compaction(messages, trigger_chars):
        return messages
    system = [m for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") != "system"]
    if len(convo) <= keep_recent + 1:
        return messages
    older, recent = convo[:-keep_recent], convo[-keep_recent:]
    summary = {"role": "user", "content": _summarize_older(older)}
    return system + [summary] + recent
