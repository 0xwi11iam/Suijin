"""banner — the dragon boot art.

The exact ansifier art (transcribed span-for-span into
suijin/assets/dragon_banner.json, 75x22 run-length grid) rendered in full
color, with the block-letter Suijin wordmark in cyan underneath.

Rules (operator contract):
- renders at EVERY TUI start (welcome, mode selector, red boot, blue
  boot, `suijin version`)
- terminal too narrow (<78 cols), non-TTY, or NO_COLOR → render NOTHING
  at all — no fallback wordmark, no torn art, clean silence
"""

from __future__ import annotations

import json
import os
from pathlib import Path

WORDMARK = r"""
______            _       _   _
.' ____ \          (_)     (_) (__)
| (___ \_|__   _   __      __  __   _ .--.
 _.____`.[  | | | [  |    [  |[  | [ `.-. |
| \____) || \_/ |, | |  _  | | | |  | | | |
 \______.''.__.'_/[___][ \_| |[___][___||__]
                        \____/
"""

_GRID: dict | None = None


def _grid() -> dict:
    global _GRID
    if _GRID is None:
        p = Path(__file__).resolve().parents[3] / "assets" / "dragon_banner.json"
        _GRID = json.loads(p.read_text())
    return _GRID


def _terminal_width(console) -> int:
    try:
        return int(console.size.width)
    except Exception:  # noqa: BLE001
        return 0


def render_boot_banner(console) -> bool:
    """True when the banner rendered; False when skipped (narrow/flat)."""
    try:
        if os.environ.get("NO_COLOR"):
            return False
        width = _terminal_width(console)
        if width < 78 or not getattr(console, "is_terminal", False):
            return False

        from rich.text import Text

        g = _grid()
        art = Text(no_wrap=True)
        for row_runs in g["rows"]:
            line = Text(no_wrap=True)
            for count, ch, bg, fg in row_runs:
                if bg == "#000000" and fg == "#800000":
                    # the black frame — plain spaces (terminal bg does the rest)
                    line.append(ch * count)
                else:
                    line.append(ch * count, style=f"{fg} on {bg}")
            art.append_text(line)
            art.append("\n")

        console.print(art, no_wrap=True, overflow="ignore")
        console.print()
        pad = max(0, (width - max(len(ln) for ln in WORDMARK.splitlines())) // 2 - 6)
        for ln in WORDMARK.strip("\n").splitlines():
            console.print(" " * pad + f"[bold cyan]{ln}[/bold cyan]", highlight=False)
        console.print()
        return True
    except Exception:  # noqa: BLE001 — the banner must never break a boot
        return False
