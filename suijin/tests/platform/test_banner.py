"""banner — the letter-density dragon: all cyan, white eye, skip rules."""

import sys

from suijin.modules.platform.lib.banner import (
    EYE_MARKS,
    WORDMARK,
    _lines,
    _render_line,
    art_width,
    render_boot_banner,
)


class _TtyShim:
    def __init__(self, width=110, tty=True):
        self._buf = []
        self._tty = tty
        self._width = width
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


def _render(width=110, tty=True):
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
    def test_art_lines_load(self):
        lines = _lines()
        assert 35 <= len(lines) <= 50  # 41 rows after the horn-section trim
        assert art_width() >= 80

    def test_eye_marks_present_in_art(self):
        lines = _lines()
        for mark in EYE_MARKS:
            assert any(mark in ln for ln in lines), f"eye mark {mark!r} missing"
            # each mark appears exactly once (unambiguous white target)
            assert sum(mark in ln for ln in lines) == 1


class TestRenderLine:
    def test_plain_line_all_cyan(self):
        out = _render_line(0, "OQI              UR")
        assert out.startswith("\x1b[36m") and out.endswith("\x1b[0m")
        assert "\x1b[97m" not in out

    def test_eye_line_white_segment(self):
        line = next(ln for ln in _lines() if "NNOT" in ln)
        out = _render_line(0, line)
        assert "\x1b[97mNNOT" in out
        assert out.index("\x1b[97m") > out.index("\x1b[36m")  # cyan pre, white eye

    def test_blank_line_empty(self):
        assert _render_line(2, "   ") == ""


class TestRender:
    def test_renders_on_wide_tty(self):
        ok, out = _render(110)
        assert ok is True
        assert out.count("\x1b[36m") > 40  # the art, cyan
        assert out.count("\x1b[97m") == 3  # the three eye marks, white
        assert "\x1b[1;36m/ ___|" in out or "/ ___|" in out  # wordmark

    def test_narrow_renders_nothing(self):
        ok, out = _render(70)
        assert ok is False
        assert out == ""

    def test_no_tty_skips(self):
        ok, out = _render(110, tty=False)
        assert ok is False
        assert out == ""

    def test_no_color_env_skips(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        ok, out = _render(110)
        assert ok is False

    def test_wordmark_exact(self):
        assert "/ ___|" in WORDMARK
        assert "_ __" in WORDMARK
        assert "|__/" in WORDMARK
