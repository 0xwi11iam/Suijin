"""Context compaction (A7) — survive long engagements.

When conversation history grows past a share of the token budget, older
tool-result messages are compressed into a single summary message (what
was tried, what worked, current facts). The system prompt and the most
recent K exchanges are preserved verbatim.
"""

from __future__ import annotations

DEFAULT_TRIGGER_CHARS = 120_000  # ~30k tokens of history
# messages kept verbatim at the tail — 16 for a 1M-token window is still
# conservative; the old 8 amputated the working set mid-chain (measured:
# a 900k-token engagement with only 13.8k of live context — the agent
# was effectively amnesiac, forgetting every observation older than 8
# exchanges)
KEEP_RECENT = 16


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
    mutates the input; no-op under the trigger.

    FORCE mode (trigger_chars=0, the operator's /compact): never refuses
    on message COUNT — a conversation of 10 giant tool outputs (300k
    chars) was uncompactable because the guard counted messages. Under
    the floor the keep-window shrinks adaptively instead."""
    forced = int(trigger_chars or 0) == 0 and bool(messages)
    if not needs_compaction(messages, trigger_chars) and not forced:
        return messages
    system = [m for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") != "system"]
    if forced:
        # adaptive floor: keep a third (min 4, max KEEP_RECENT) — there is
        # always something to summarize unless the convo is tiny
        keep = max(4, min(keep_recent, len(convo) // 3))
        if len(convo) <= keep + 1:
            # genuinely tiny (≤5 messages): nothing older than the floor
            return messages
    else:
        keep = keep_recent
        if len(convo) <= keep + 1:
            return messages
    older, recent = convo[:-keep], convo[-keep:]
    summary = {"role": "user", "content": _summarize_older(older)}
    return system + [summary] + recent
