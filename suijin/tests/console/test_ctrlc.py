"""Ctrl/C handling — the TUI must never traceback on SIGINT."""

import builtins
from unittest import mock

import pytest


class TestWelcomePromptCtrlC:
    def test_keyboardinterrupt_exits_clean(self, capsys):
        import suijin.main as m

        with (
            mock.patch.object(builtins, "input", side_effect=KeyboardInterrupt),
            mock.patch("rich.console.Console.print"),
            mock.patch("suijin.modules.platform.lib.runtime.init_runtime"),
            mock.patch("suijin.modules.tools.lib.availability.startup_banner", return_value=""),
        ):
            # main() should return WITHOUT raising after ^C at the prompt
            m.main()

    def test_eof_exits_clean(self):
        import suijin.main as m

        with (
            mock.patch.object(builtins, "input", side_effect=EOFError),
            mock.patch("rich.console.Console.print"),
            mock.patch("suijin.modules.platform.lib.runtime.init_runtime"),
            mock.patch("suijin.modules.tools.lib.availability.startup_banner", return_value=""),
        ):
            m.main()


class TestUmbrella:
    def test_tui_keyboardinterrupt_exit_130(self, monkeypatch):
        from suijin.modules.console.lib import cli

        def boom():
            raise KeyboardInterrupt()

        monkeypatch.setattr("suijin.main.main", boom)
        with pytest.raises(SystemExit) as ei:
            cli.main([])
        assert ei.value.code == 130
