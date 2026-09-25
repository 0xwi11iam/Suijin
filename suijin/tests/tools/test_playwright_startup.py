"""Browser startup must FAIL LOUDLY AND FAST, never hang.

The field bug: `playwright` was importable but its chromium binary had
never been downloaded. `pw.chromium.launch()` raised inside the browser
thread, so `_browser_ready.set()` — the line every caller blocks on —
never ran. The thread died, so `_start()` saw "not alive" and relaunched
it on the NEXT call, meaning every browser tool call paid the full 30s
timeout and then reported a generic "Browser startup timed out" that
never mentioned the actual fix (`playwright install chromium`).
"""

from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture()
def mp():
    """A fresh import of the pack with its browser state reset."""
    import importlib
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "modules" / "mcp_playwright"
    sys.path.insert(0, str(root))
    try:
        import main as mod  # type: ignore[import-not-found]

        mod = importlib.reload(mod)
        mod._browser_ready.clear()
        mod._browser_error = ""
        mod._browser_thread = None
        yield mod
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("main", None)


def _fake_playwright(monkeypatch, launch_exc: Exception | None = None, start_exc: Exception | None = None):
    """Install a believable playwright pair whose launch (or start)
    raises, so the startup path is exercised for real."""
    import sys
    import types

    def _sync_playwright():
        if start_exc is not None:
            raise start_exc

        class _Chromium:
            @staticmethod
            def launch(**_kw):
                if launch_exc is not None:
                    raise launch_exc
                raise AssertionError("test never launches a real browser")

        class _PW:
            chromium = _Chromium()

            @staticmethod
            def stop():
                return None

        class _Context:
            @staticmethod
            def start():
                return _PW()

            @staticmethod
            def stop():
                return None

        return _Context()

    root = types.ModuleType("playwright")
    root.__path__ = []  # make it a package so the submodule import resolves
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = _sync_playwright
    root.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", root)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)


class TestBrowserStartupFailures:
    def test_missing_chromium_fails_immediately_with_the_fix(self, mp, monkeypatch):
        """The launch failure must be caught, reported, and must NOT cost
        30 seconds."""
        _fake_playwright(
            monkeypatch,
            launch_exc=RuntimeError("Executable doesn't exist at ~/Library/Caches/ms-playwright"),
        )
        monkeypatch.setattr(mp, "_browser_thread", None)

        started = time.monotonic()
        err = mp._start()
        elapsed = time.monotonic() - started

        assert err, "a failed launch must return an error, not None"
        assert "playwright install chromium" in err, f"the actionable fix is missing: {err}"
        assert "Executable doesn't exist" in err, f"the real error was swallowed: {err}"
        assert elapsed < 5, f"startup took {elapsed:.1f}s — the 30s hang is back"

    def test_waiters_are_always_released(self, mp, monkeypatch):
        """_browser_ready must fire even when startup blows up — that is
        the only thing standing between a failed launch and a timeout."""
        _fake_playwright(monkeypatch, launch_exc=RuntimeError("driver failed to start"))
        monkeypatch.setattr(mp, "_browser_thread", None)

        mp._browser_ready.clear()
        thread = threading.Thread(target=mp._browser_loop, daemon=True)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), "the browser thread hung instead of failing"
        assert mp._browser_ready.is_set(), "waiters were never released on failure"

    def test_no_unhandled_exception_escapes_the_thread(self, mp, monkeypatch):
        """An exception out of the thread is what produced the unhandled
        PytestUnhandledThreadExceptionWarning; it must stay contained."""
        _fake_playwright(monkeypatch, start_exc=RuntimeError("nope"))

        mp._browser_ready.clear()
        t = threading.Thread(target=mp._browser_loop, daemon=True)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive()
        assert mp._browser_error, "the failure reason was not recorded"
