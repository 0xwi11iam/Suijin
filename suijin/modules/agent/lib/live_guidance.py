"""Live guidance — the file-based channel from operator to AI.

The operator types in the input box → the line lands in
engagement_dir()/live_guidance.md → the think node reads it on EVERY
invocation (at the TOP of the system prompt, above everything) →
truncates the file (consumed). No LangGraph state mutation, no
update_state, no concurrency — a file read is atomic and cannot fail.

context.md — the live manifest of what the AI was fed (overwritten each
think turn): guidance verbatim, phase, iteration, recent actions, prompt
sizes. The operator can tail -f to see what the AI sees in real time.
"""

from __future__ import annotations

from pathlib import Path


def guidance_path() -> Path:
    from suijin.modules.platform.lib.workspace import engagement_dir

    return engagement_dir() / "live_guidance.md"


def context_path() -> Path:
    from suijin.modules.platform.lib.workspace import engagement_dir

    return engagement_dir() / "context.md"


def write_guidance(line: str, mode: str = "") -> None:
    """Append one operator guidance line. The think node consumes it."""
    import time

    try:
        p = guidance_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H:%M:%S")
        tag = f"[{mode.upper()}] " if mode else ""
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"- {stamp} {tag}{str(line).strip()}\n")
    except OSError:
        pass  # guidance must never break the input loop


def read_and_clear_guidance() -> str:
    """Read all pending guidance, return as a section body, truncate.
    Empty string when nothing is pending."""
    try:
        p = guidance_path()
        if not p.is_file():
            return ""
        body = p.read_text(encoding="utf-8", errors="ignore").strip()
        if body:
            p.write_text("", encoding="utf-8")  # consumed
        return body
    except OSError:
        return ""


def write_context_manifest(
    *,
    guidance: str,
    phase: str,
    iteration: int,
    attack_path: str,
    recent_actions: str,
    msg_count: int,
    prompt_chars: int,
) -> None:
    """Overwrite the live context manifest — what the AI was fed THIS turn."""
    import time

    try:
        p = context_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        g = (
            f"## OPERATOR GUIDANCE (delivered this turn)\n{guidance}\n"
            if guidance
            else "## OPERATOR GUIDANCE\n(none pending)\n"
        )
        p.write_text(
            f"# Context Manifest — {time.strftime('%H:%M:%S')} every think turn\n\n"
            f"{g}\n"
            f"## STATE\n"
            f"- phase: {phase}\n"
            f"- iteration: {iteration}\n"
            f"- attack_path: {attack_path}\n"
            f"- messages in state: {msg_count}\n"
            f"- system prompt: {prompt_chars:,} chars\n\n"
            f"## RECENT ACTIONS\n{recent_actions or '(none)'}\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # the manifest must never break thinking
