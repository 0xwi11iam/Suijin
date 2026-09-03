"""banner — the dragon boot art: render, skip rules, wordmark."""

import os
import sys

from rich.console import Console

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.platform.lib.banner import WORDMARK, _grid, render_boot_banner  # noqa: E402


def _tty(width=100):
    return Console(force_terminal=True, width=width, color_system="truecolor", file=sys.stdout)


class TestGrid:
    def test_grid_loads(self):
        g = _grid()
        assert g["width"] == 75 and g["height"] == 22
        # every row expands to exactly 75 cells
        for row in g["rows"]:
            assert sum(r[0] for r in row) == 75

    def test_art_cells_present(self):
        g = _grid()
        glyphs = "".join(ch * n for row in g["rows"] for n, ch, *_ in row)
        assert glyphs.count("▓") > 30 and glyphs.count("▒") > 80 and glyphs.count("░") > 60


class TestRender:
    def test_renders_on_wide_tty(self, capsys):
        c = _tty(100)
        assert render_boot_banner(c) is True
        out = capsys.readouterr().out
        assert any(ch in out for ch in "▓▒░")
        assert ".--." in out  # the cyan wordmark

    def test_narrow_renders_nothing(self, capsys):
        c = _tty(70)
        assert render_boot_banner(c) is False
        assert capsys.readouterr().out == ""  # NOTHING — no fallback, no torn art

    def test_no_color_env_skips(self, capsys, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        c = _tty(100)
        assert render_boot_banner(c) is False
        assert capsys.readouterr().out == ""

    def test_non_tty_skips(self, capsys):
        from io import StringIO

        c = Console(file=StringIO(), width=100, force_terminal=False)
        assert render_boot_banner(c) is False

    def test_wordmark_exact(self):
        # the operator's exact block text
        assert ".--." in WORDMARK
        assert "[___]" in WORDMARK
        assert "\\____/" in WORDMARK
