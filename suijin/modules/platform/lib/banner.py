"""banner — the letter-density dragon: cyan + vertical red stripes, white eye.

The art ships as assets/banner.txt (plain text; the shape lives in the
leading spaces). Base color cyan with RED VERTICAL STRIPES — irregular
column bands running down the full length of the body (deterministic
pseudo-random column selection, ~2/13 of columns); the eye region —
the NNOT / G CEK / RTRO letter cluster — renders bright white.

Rules (operator contract):
- renders at EVERY TUI start (welcome, mode selector, red boot, blue
  boot, `suijin version`)
- terminal too narrow, non-TTY, or NO_COLOR → NOTHING at all
- the wordmark (block text) prints cyan; on the STARTUP SCREEN the
  version block-art (v green, digits alternating red/blue) prints to
  the RIGHT of the wordmark on the same lines
"""

from __future__ import annotations

import json
import os
import re
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

RED = "\x1b[31;1m"
CYAN = "\x1b[36m"

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


def _col_is_red(col: int) -> bool:
    """Deterministic pseudo-random vertical stripes: irregular column
    bands running down the full body length (~2/13 of columns)."""
    return ((col * 31 + 7) % 13) < 2


def _render_line(idx: int, line: str) -> str:
    """One art line: per-column stripes (cyan/red), white across the eye."""
    if not line.rstrip():
        return ""
    out = []
    # find the eye mark range (columns to force white)
    mark = next((m for m in EYE_MARKS if m in line), None)
    eye_start = eye_end = -1
    if mark:
        eye_start = line.index(mark)
        eye_end = eye_start + len(mark)
    for col, ch in enumerate(line.rstrip()):
        if eye_start <= col < eye_end:
            out.append("\x1b[97m" + ch)
        elif _col_is_red(col):
            out.append(RED + ch)
        else:
            out.append(CYAN + ch)
    return "".join(out) + "\x1b[0m"


# ── the version block-art ────────────────────────────────────────────

_DIGITS: dict[str, tuple[str, str, str, str, str]] = {
    "v": ("       ", " __   __", " \\ \\ / /", "  \\ V / ", "   \\_/  "),
    ".": ("   ", "   ", "   ", " _ ", "(_)"),
    "0": ("  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "),
    "1": (" _ ", "/ |", "| |", "| |", "|_|"),
    "2": (" ____  ", "|___ \\ ", "  __) |", " / __/ ", "|_____|"),
    "3": (" _____ ", "|___  |", "  _| | ", " |_| | ", "|____/ "),
    "4": (" _  _   ", "| || |  ", "| || |_ ", "|__   _|", "   |_|  "),
    "5": (" ____  ", "| ___| ", "| |_   ", "|  _| | ", "|_|   | "),
    "6": ("  ____ ", " / ___|", "| | _ ", "| || |", "| || |"),
    "7": (" _____ ", "|___  |", "   / / ", "  / /  ", " /_/   "),
    "8": ("  ___  ", " ( _ ) ", " / _ \\ ", "| |_| |", " \\___/ "),
    "9": ("  ___  ", " / _ \\ ", "| |_| |", " \\__, |", "  /_/  "),
}


def _version_lines() -> list[str] | None:
    """The version as 5 colored lines — v green, digits red/blue, dots dim."""
    try:
        vj = json.loads((Path(__file__).resolve().parents[3] / "version.json").read_text())
        v = str(vj.get("version", ""))
        if not v or any(ch not in _DIGITS for ch in v):
            return None
        rows = []
        for r in range(5):
            buf = []
            for i, ch in enumerate("v" + v):
                piece = _DIGITS[ch][r] + " "
                if ch == "v":
                    buf.append("\x1b[32m" + piece + "\x1b[0m")
                elif ch == ".":
                    buf.append("\x1b[90m" + piece + "\x1b[0m")
                else:
                    color = "\x1b[31;1m" if i % 2 == 0 else "\x1b[34;1m"
                    buf.append(color + piece + "\x1b[0m")
            rows.append("".join(buf).rstrip())
        return rows
    except Exception:  # noqa: BLE001
        return None


def _terminal_width() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except Exception:  # noqa: BLE001
        return 0


def render_boot_banner(console=None, version: bool = False) -> bool:
    """True when rendered; False when skipped (narrow/flat). Raw output.

    version=True → the block-art version renders to the RIGHT of the
    wordmark on the same lines (startup screen only)."""
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
        # the wordmark — cyan; version art to its RIGHT when requested
        mark_lines = WORDMARK.split("\n")
        mark_w = max(len(ln) for ln in mark_lines)
        v_lines = _version_lines() if version else None
        if v_lines:
            total_w = mark_w + 4 + max(len(re.sub(r"\x1b\[[0-9;]*m", "", ln)) for ln in v_lines)
            pad = max(0, (width - total_w) // 2)
            out.write("\n")
            for r in range(max(len(mark_lines), len(v_lines))):
                mark_ln = mark_lines[r] if r < len(mark_lines) else " " * mark_w
                v_ln = v_lines[r] if r < len(v_lines) else ""
                out.write(" " * pad + "\x1b[1;36m" + mark_ln + "\x1b[0m" + "    " + v_ln + "\n")
            out.write("\n")
        else:
            pad = max(0, (width - mark_w) // 2)
            out.write("\n")
            for ln in mark_lines:
                out.write(" " * pad + "\x1b[1;36m" + ln + "\x1b[0m\n")
            out.write("\n")
        out.flush()
        return True
    except Exception:  # noqa: BLE001 — the banner must never break a boot
        return False
