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
        assert 42 <= len(lines) <= 52  # 47 rows: horns kept, only the two stray top pairs trimmed
        assert art_width() >= 80

    def test_horn_tips_present(self):
        blob = "\n".join(_lines())
        for tip in ("VU", "WYX", "XXXWW", "WXXXXXYYYX"):
            assert tip in blob, f"horn tip {tip!r} missing"
        assert "OQI" not in blob and "GTP" not in blob  # the removed strays

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
        # per-column coloring: each eye char is individually white
        assert out.count("\x1b[97mN") >= 2  # N,N of NNOT
        assert out.count("\x1b[97m") >= 4  # the eye characters

    def test_blank_line_empty(self):
        assert _render_line(2, "   ") == ""


class TestRender:
    def test_renders_on_wide_tty(self):
        ok, out = _render(110)
        assert ok is True
        assert out.count("\x1b[36m") > 20  # cyan rows
        assert out.count("\x1b[31;1m") >= 10  # the red stripe bands
        assert out.count("\x1b[97m") >= 10  # the eye chars (per-column: NNOT+G CEK+RTRO)
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
