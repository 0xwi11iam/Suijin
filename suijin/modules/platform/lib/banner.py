"""banner — the letter-density dragon: cyan base, red bands, white eye.

styled_lines() is the SINGLE source of dragon styling — both render
paths consume it (the raw-ANSI boot banner and the Textual Shell's
DragonWidget), so the red accent appears on every dragon render.

Band pattern: every 7th row group — rows where (index % 7) >= 4 — is
bright red (3-row red bands separated by 4 cyan rows). One red shade.
Eye marks (content-based) stay bright white regardless of band.
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

EYE_MARKS = ("NNOT", "G CEK", "RTRO")

CYAN, RED, EYE = "cyan", "red", "eye"

_LINES: list[str] | None = None


def _lines() -> list[str]:
    global _LINES
    if _LINES is None:
        p = Path(__file__).resolve().parents[3] / "assets" / "banner.txt"
        _LINES = p.read_text().rstrip("\n").split("\n")
    return _LINES


def _row_style(idx: int) -> str:
    """Red band: rows 4,5,6 of every 7-row group."""
    return RED if (idx % 7) >= 4 else CYAN


def styled_lines() -> list[list[tuple[str, str]]]:
    """Per art line: [(text, style), ...] segments. style ∈ {cyan, red, eye}.

    The eye mark splits its line into pre/eye/post; the rest of the line
    carries the row's band color. Blank lines return []."""
    out: list[list[tuple[str, str]]] = []
    for idx, line in enumerate(_lines()):
        if not line.rstrip():
            out.append([])
            continue
        base = _row_style(idx)
        mark = next((m for m in EYE_MARKS if m in line), None)
        if mark:
            start = line.index(mark)
            end = start + len(mark)
            segs: list[tuple[str, str]] = []
            if line[:start]:
                segs.append((line[:start], base))
            segs.append((line[start:end], EYE))
            if line[end:].rstrip():
                segs.append((line[end:].rstrip(), base))
            out.append(segs)
        else:
            out.append([(line.rstrip(), base)])
    return out


def art_width() -> int:
    try:
        return max(len(ln.rstrip()) for ln in _lines())
    except Exception:  # noqa: BLE001
        return 0


_ANSI = {CYAN: "\x1b[36m", RED: "\x1b[31;1m", EYE: "\x1b[97m"}


def _terminal_width() -> int:
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except Exception:  # noqa: BLE001
        return 0


def render_boot_banner(console=None) -> bool:
    """Raw-ANSI boot banner (welcome/red boot/blue boot/`suijin version`).
    True when rendered; False when skipped (narrow/flat)."""
    try:
        if os.environ.get("NO_COLOR"):
            return False
        width = _terminal_width()
        if width < art_width() + 2:
            return False
        if not (sys.stdout.isatty() or (console is not None and getattr(console, "is_terminal", False))):
            return False

        out = sys.stdout
        for segs in styled_lines():
            if not segs:
                out.write("\n")
                continue
            for text, style in segs:
                out.write(_ANSI[style] + text)
            out.write("\x1b[0m\n")
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
