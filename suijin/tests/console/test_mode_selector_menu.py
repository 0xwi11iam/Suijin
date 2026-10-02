"""The mode selector's menu — every mode must be REACHABLE.

Regression: the v6.9.0 "Focus Release" commit trimmed the selector from
five options to three and silently dropped Settings. The Textual settings
TUI survived as an orphan module with no way to open it. These tests pin
the exact menu — a row that disappears (or reappears) is a FAILURE, not
a quiet feature reduction nobody notices until they go looking for it.

The operator's menu (2026-09-24): Red Team / Settings / Operator Tools /
Exit. Blue Team is NOT launched from the selector (the blue modules stay
in the tree — the gateway, defenders and blue tools still use them).
"""

from __future__ import annotations

from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "main.py"

#: the exact menu rows, in order
EXPECTED_MENU = ("Red Team", "Settings", "Operator Tools", "Exit")


def _source() -> str:
    return MAIN.read_text(encoding="utf-8")


def _selector_block() -> str:
    """The selector's print + dispatch block (the first `except` AFTER the
    menu header — the file has earlier excepts in the operator menu)."""
    src = _source()
    start = src.index("Select Operational Module")
    end = src.index("except (KeyboardInterrupt, EOFError)", start)
    return src[start:end]
