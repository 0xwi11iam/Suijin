"""banner — the dragon boot art (raw ANSI passthrough).

The art ships as assets/banner.ans — the exact ansifier-style escape
stream (xterm-256 SGR per cell, one char per cell), generated from the
validated 75x22 grid. It is written RAW to the terminal stream: Rich's
Text renderer mis-measures the half-block glyphs (▓▒░) and squashes the
art — the hydraulic-press incident. Raw bytes render cell-perfect.

The block-letter wordmark underneath, cyan.

Rules (operator contract):
- renders at EVERY TUI start (welcome, mode selector, red boot, blue
  boot, `suijin version`)
- terminal too narrow (<80 cols — the art is 78 wide), non-TTY, or
  NO_COLOR → NOTHING at all
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORDMARK = (
    " ____        _  _ _       \n"
    "/ ___| _   _(_)(_|_)_ __  \n"
    "\\___ \\| | | | || | | '_ \\ \n"
    " ___) | |_| | || | | | | |\n"
    "|____/ \\__,_|_|/ |_|_| |_|\n"
    "             |__/ "
)

_ANS: str | None = None


def _ans() -> str:
    global _ANS
    if _ANS is None:
        p = Path(__file__).resolve().parents[3] / "assets" / "banner.ans"
        _ANS = p.read_text()
    return _ANS


def _terminal_width() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except Exception:  # noqa: BLE001
        return 0


def render_boot_banner(console=None) -> bool:
    """True when rendered; False when skipped (narrow/flat). Raw output —
    no Rich anywhere near the art."""
    try:
        if os.environ.get("NO_COLOR"):
            return False
        width = _terminal_width()
        if width < 80:
            return False
        if not (sys.stdout.isatty() or (console is not None and getattr(console, "is_terminal", False))):
            return False

        out = sys.stdout
        out.write(_ans())  # the dragon, verbatim
        # the wordmark — cyan, centered-ish under the art
        mark_lines = WORDMARK.strip("\n").splitlines()
        mark_w = max(len(ln) for ln in mark_lines)
        pad = max(0, (width - mark_w) // 2)
        out.write("\n")
        for ln in mark_lines:
            out.write(" " * pad + "\x1b[1;36m" + ln + "\x1b[0m\n")
        out.write("\n")
        out.flush()
        return True
    except Exception:  # noqa: BLE001 — the banner must never break a boot
        return False
