"""banner — the letter-density dragon, all cyan, white eye.

The art ships as assets/banner.txt (plain text; the shape lives in the
leading spaces). Every character renders cyan; the eye region — the
NNOT / G CEK / RTRO letter cluster — renders bright white.

Rules (operator contract):
- renders at EVERY TUI start (welcome, mode selector, red boot, blue
  boot, `suijin version`)
- terminal too narrow, non-TTY, or NO_COLOR → NOTHING at all
- the wordmark (block text) prints cyan underneath
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

# the eye: each mark renders bright white on whichever line carries it
# (content-based — line indices shift when the art is edited)
EYE_MARKS = ("NNOT", "G CEK", "RTRO")

_LINES: list[str] | None = None


def _lines() -> list[str]:
    global _LINES
    if _LINES is None:
        p = Path(__file__).resolve().parents[3] / "assets" / "banner.txt"
        _LINES = p.read_text().rstrip("\n").split("\n")
    return _LINES


def art_width() -> int:
    try:
        return max(len(ln.rstrip()) for ln in _lines())
    except Exception:  # noqa: BLE001
        return 0


def _render_line(idx: int, line: str) -> str:
    """One art line: cyan everywhere, bright white across the eye mark."""
    mark = next((m for m in EYE_MARKS if m in line), None)
    if mark:
        start = line.index(mark)
        end = start + len(mark)
        pre, eye, post = line[:start], line[start:end], line[end:]
        out = ""
        if pre.strip() or pre:
            out += "\x1b[36m" + pre
        out += "\x1b[97m" + eye
        if post.rstrip():
            out += "\x1b[36m" + post
        return out.rstrip() + "\x1b[0m"
    if not line.rstrip():
        return ""
    return "\x1b[36m" + line.rstrip() + "\x1b[0m"


def _terminal_width() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except Exception:  # noqa: BLE001
        return 0


def render_boot_banner(console=None) -> bool:
    """True when rendered; False when skipped (narrow/flat). Raw output."""
    try:
        if os.environ.get("NO_COLOR"):
            return False
        width = _terminal_width()
        need = art_width() + 2
        if width < need:
            return False
        if not (sys.stdout.isatty() or (console is not None and getattr(console, "is_terminal", False))):
            return False

        out = sys.stdout
        for idx, line in enumerate(_lines()):
            out.write(_render_line(idx, line) + "\n")
        # the wordmark — cyan, centered under the art
        mark_lines = WORDMARK.split("\n")
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
