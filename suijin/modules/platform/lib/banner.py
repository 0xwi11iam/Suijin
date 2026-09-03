"""banner — the letter-density dragon: cyan + red bands, white eye.

The art ships as assets/banner.txt (plain text; the shape lives in the
leading spaces). Base color cyan with RED BANDS (rows 4-6 of every 7-row
group — horizontal red striping); the eye region — the NNOT / G CEK /
RTRO letter cluster — renders bright white.

Rules (operator contract):
- renders at EVERY TUI start (welcome, mode selector, red boot, blue
  boot, `suijin version`)
- terminal too narrow, non-TTY, or NO_COLOR → NOTHING at all
- the wordmark (block text) prints cyan underneath
- the version block-art (v green, digits alternating red/blue) prints
  on the STARTUP SCREEN ONLY (welcome) — not every banner render
"""

from __future__ import annotations

import json
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

RED = "\x1b[31;1m"  # the one bright-red shade — the stripes

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


def _row_color(idx: int) -> str:
    """Red band: rows 4,5,6 of every 7-row group (3 red / 4 cyan stripes)."""
    return RED if (idx % 7) >= 4 else "\x1b[36m"


def _render_line(idx: int, line: str) -> str:
    """One art line: band color everywhere, bright white across the eye."""
    mark = next((m for m in EYE_MARKS if m in line), None)
    if mark:
        start = line.index(mark)
        end = start + len(mark)
        pre, eye, post = line[:start], line[start:end], line[end:]
        out = ""
        if pre.strip() or pre:
            out += _row_color(idx) + pre
        out += "\x1b[97m" + eye
        if post.rstrip():
            out += _row_color(idx) + post
        return out.rstrip() + "\x1b[0m"
    if not line.rstrip():
        return ""
    return _row_color(idx) + line.rstrip() + "\x1b[0m"


# ── the version block-art (startup screen only) ──────────────────────

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


def render_version_art() -> bool:
    """v6.6.x in block digits under the wordmark — STARTUP SCREEN ONLY.
    `v` green; digits alternate red/blue per position; dots dim."""
    try:
        if os.environ.get("NO_COLOR"):
            return False
        vj = json.loads((Path(__file__).resolve().parents[3] / "version.json").read_text())
        v = str(vj.get("version", ""))
        if not v or any(ch not in _DIGITS for ch in v):
            return False

        out = sys.stdout
        for r in range(5):
            buf = []
            for i, ch in enumerate("v" + v):
                g = _DIGITS[ch][r] + " "
                if ch == "v":
                    buf.append("\x1b[32m" + g + "\x1b[0m")  # green
                elif ch == ".":
                    buf.append("\x1b[90m" + g + "\x1b[0m")  # dim
                else:
                    color = "\x1b[31;1m" if i % 2 == 0 else "\x1b[34;1m"  # red/blue alternating
                    buf.append(color + g + "\x1b[0m")
            out.write("".join(buf).rstrip() + "\n")
        out.flush()
        return True
    except Exception:  # noqa: BLE001 — version art must never break a boot
        return False


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
