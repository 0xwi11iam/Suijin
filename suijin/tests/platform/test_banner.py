"""banner — the letter-density dragon: cyan + red bands + white eye."""

import sys

from suijin.modules.platform.lib.banner import (
    CYAN,
    EYE,
    EYE_MARKS,
    RED,
    WORDMARK,
    _lines,
    art_width,
    render_boot_banner,
    styled_lines,
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
        assert 42 <= len(lines) <= 52  # 47 rows: horns kept, strays trimmed
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
            assert sum(mark in ln for ln in lines) == 1


class TestStyledLines:
    def test_band_pattern(self):
        segs = styled_lines()
        for idx, row in enumerate(segs):
            if not row:
                continue
            base = next(s for _, s in row if s != EYE)
            assert base == (RED if idx % 7 >= 4 else CYAN), f"row {idx}: {base}"

    def test_eye_segments(self):
        segs = styled_lines()
        assert sum(1 for row in segs for _, s in row if s == EYE) == 3

    def test_red_rows_exist(self):
        segs = styled_lines()
        red_rows = sum(1 for row in segs if any(s == RED for _, s in row))
        assert red_rows >= 12  # ~3/7 of 47 rows


class TestRender:
    def test_renders_on_wide_tty_with_red_bands(self):
        ok, out = _render(110)
        assert ok is True
        assert out.count("\x1b[31;1m") >= 12  # the red band runs
        assert out.count("\x1b[97m") == 3  # the eye
        assert "\x1b[1;36m/ ___|" in out  # the cyan wordmark

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
