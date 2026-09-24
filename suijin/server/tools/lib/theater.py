"""Session theater (C26) — animated step-through of an engagement."""

from __future__ import annotations

import time


def render_frames(session: dict, width: int = 60) -> list[str]:
    """One ASCII frame per iteration: progress bar + step summary."""
    iterations = session.get("iterations") or []
    frames = []
    total = len(iterations)
    for i, step in enumerate(iterations, 1):
        filled = int(width * i / max(total, 1))
        bar = "#" * filled + "-" * (width - filled)
        ok = "OK " if step.get("success", True) else "FAIL"
        tool = step.get("tool", step.get("tool_name", "?"))
        thought = str(step.get("thought", ""))[:40]
        frames.append(f"[{bar}] {i}/{total} {ok} {tool} — {thought}")
    return frames


def play(session: dict, delay: float = 0.15, out=print) -> None:
    """Print frames with pacing (the 'theater')."""
    for frame in render_frames(session):
        out(frame)
        time.sleep(max(0.0, min(delay, 2.0)))
