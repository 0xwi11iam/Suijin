"""banner — the dragon boot art: raw ANSI passthrough, skip rules, wordmark."""

import sys

from suijin.modules.platform.lib.banner import WORDMARK, _ans, render_boot_banner


class _TtyShim:
    """A stdout shim that looks like a real 100-col terminal."""

    def __init__(self, width=100, tty=True, columns=None):
        self._buf = []
        self._tty = tty
        self._columns = columns if columns is not None else width
        self.isatty = lambda: self._tty

    def fileno(self):
        return 0

    def write(self, s):
        self._buf.append(s)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self._buf)


def _render(width=100, tty=True):
    from suijin.modules.platform.lib import banner as _b

    shim = _TtyShim(width, tty)
    old, old_w = sys.stdout, _b._terminal_width
    sys.stdout = shim
    _b._terminal_width = lambda: width
    try:
        ok = render_boot_banner()
    finally:
        sys.stdout = old
        _b._terminal_width = old_w
    return ok, shim.text


class TestArt:
    def test_ans_ships_and_is_wellformed(self):
        src = _ans()
        lines = src.strip("\n").split("\n")
        assert 30 <= len(lines) <= 45  # the dragon art is 37 rows
        import re

        for ln in lines:
            plain = re.sub(r"\x1b\[[0-9;]*m", "", ln)
            assert len(plain.rstrip()) <= 80  # art is ~77 wide after frame-crop

    def test_colored_cells_present(self):
        import re

        src = _ans()
        runs = re.findall(r"\x1b\[48;5;(\d+)m", src)
        colored = [int(r) for r in runs if int(r) not in (0, 16)]
        assert len(colored) > 500  # the bg-only gradient art
        assert "▓" not in src and "▒" not in src  # bg-only: no glyphs at all


class TestRender:
    def test_renders_raw_on_wide_tty(self):
        ok, out = _render(200)
        assert ok is True
        assert _ans() in out  # the art bytes, VERBATIM
        assert "\x1b[1;36m" in out  # cyan wordmark

    def test_narrow_renders_nothing(self):
        ok, out = _render(70)
        assert ok is False
        assert out == ""  # NOTHING — no fallback, no torn art

    def test_no_tty_skips(self):
        ok, out = _render(200, tty=False)
        assert ok is False
        assert out == ""

    def test_no_color_env_skips(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        ok, out = _render(200)
        assert ok is False

    def test_wordmark_exact(self):
        assert "_______." in WORDMARK
        assert "(----`" in WORDMARK
        assert r"\______/" in WORDMARK
