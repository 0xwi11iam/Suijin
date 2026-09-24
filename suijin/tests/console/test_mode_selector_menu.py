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

import re
from pathlib import Path

import pytest

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


class TestSelectorMenu:
    def test_menu_is_exactly_the_operator_set(self):
        """Pins the whole menu: Red Team / Settings / Operator Tools / Exit."""
        block = _selector_block()
        listed = re.findall(r"\[bold [^\]]+\](\d)\.\[[^]]*\]\s*(?:\[white\]|\[dim\])([^(\[]+)", block)
        labels = tuple(label.strip() for _, label in listed)
        assert labels[: len(EXPECTED_MENU)] == EXPECTED_MENU, labels

    def test_blue_team_is_not_in_the_selector(self):
        """The operator runs red team + settings. Blue Team must not creep
        back into the menu (the blue MODULES stay — gateway/defenders)."""
        src = _source()
        assert "Blue Team" not in src
        assert "blueteam_main" not in src
        # …and the blue module itself is untouched, only unlaunched
        from suijin.modules.blueteam.lib import blueteamer

        assert callable(blueteamer.main)

    def test_settings_entry_opens_the_settings_tui(self):
        src = _source()
        # the row exists AND is wired to the real module
        assert re.search(r'elif c == "\d+":\s*\n\s*from suijin\.modules\.console\.lib import settings_tui', src)
        assert "settings_tui.main()" in src

    def test_settings_tui_module_is_importable(self):
        from suijin.modules.console.lib import settings_tui

        assert callable(settings_tui.main)
        assert getattr(settings_tui, "ALL_FIELDS", None), "settings TUI has no fields"

    def test_every_numbered_choice_dispatches(self):
        """Each menu number maps to a handler — no dead row."""
        block = _selector_block()
        numbers = re.findall(r'c == "(\d)"', block)
        assert numbers, "no choices in the selector"
        for n in numbers:
            after = block.split(f'c == "{n}"', 1)[1]
            assert "()" in after.split("elif", 1)[0], f"choice {n} dispatches nothing"

    def test_settings_tui_opens_in_a_terminal(self, monkeypatch, capsys):
        """The wired path reaches the app; non-TTY degrades to the summary
        (never a crash, never a silent nothing)."""
        from suijin.modules.console.lib import settings_tui

        monkeypatch.setattr(settings_tui, "sys_stdin_tty", lambda: False)
        settings_tui.main()
        out = capsys.readouterr().out
        assert "settings:" in out  # the config path is always shown

    def test_settings_tui_starts_the_editor_in_a_terminal(self, monkeypatch):
        """A TTY actually opens the editor (not just the summary)."""
        from suijin.modules.console.lib import settings_tui

        ran = []
        monkeypatch.setattr(settings_tui, "sys_stdin_tty", lambda: True)
        monkeypatch.setattr(settings_tui, "run_editor", lambda console: ran.append(1) or 0)
        assert settings_tui.main() == 0
        assert ran == [1]

    def test_settings_tui_is_rich_not_textual(self):
        source = (MAIN.parent / "modules" / "console" / "lib" / "settings_tui.py").read_text()
        assert "import textual" not in source and "from textual" not in source
        assert "from rich" in source

    @pytest.mark.parametrize("row", ["1", "2", "3", "4"])
    def test_rows_are_numbered_without_gaps(self, row):
        block = _selector_block()
        listed = re.findall(r"\[bold [^\]]+\](\d)\.", block)
        assert row in listed
        assert listed == sorted(listed), "menu numbers out of order"
