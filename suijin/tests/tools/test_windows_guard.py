"""Windows-via-Docker policy — native Windows execution is refused.

The Python core happens to import anywhere, but the product contract is
macOS/Linux native or Linux-in-Docker (Windows users install via
install.ps1, which sets up the container). execute_terminal enforces it.
"""

import sys
from unittest import mock

from suijin.modules.tools.lib.terminal import execute_terminal


class TestWindowsGuard:
    def test_win32_refused_with_docker_pointer(self):
        with mock.patch.object(sys, "platform", "win32"):
            out = execute_terminal("dir")
        assert out.startswith("Error: native Windows is not supported")
        assert "install.ps1" in out
        assert "Docker" in out

    def test_posix_unchanged(self, monkeypatch):
        # a trivial command must still execute on posix platforms
        out = execute_terminal("echo guard-probe")
        assert "guard-probe" in out
