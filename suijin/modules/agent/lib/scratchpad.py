"""Engagement scratchpad (C2) — the agent's external notebook.

Coding agents re-read files; Suijin's agent re-reads ITS scratchpad.
One markdown file per workspace, injected into the conversation on the
first iteration of every engagement, appended by every write_note —
so state survives context compaction and long engagements.
"""

from __future__ import annotations

from pathlib import Path


def scratchpad_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "outputs" / "scratchpad"
    d.mkdir(parents=True, exist_ok=True)
    return d / "engagement.md"


def read_scratchpad(max_chars: int = 4000) -> str:
    p = scratchpad_path()
    if not p.exists():
        return ""
    try:
        return p.read_text(errors="ignore")[-max_chars:]
    except OSError:
        return ""


def append_note(content: str, category: str = "") -> None:
    """write_note hook — one line on the pad. Never raises."""
    try:
        import time

        stamp = time.strftime("%m-%d %H:%M")
        tag = f"[{category}] " if category else ""
        line = f"- {stamp} {tag}{str(content).strip()[:300]}"
        with scratchpad_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 — the pad must never break note-taking
        pass


def scratchpad_message() -> str | None:
    """First-turn injection; None when the pad is empty."""
    body = read_scratchpad()
    if not body.strip():
        return None
    return (
        "YOUR SCRATCHPAD — your own notes from this and previous engagements "
        "(survives everything; write_note appends to it):\n" + body
    )
