"""Container verb dispatch — the gate that keeps `docker run image version`
working while pytest's own argv NEVER lands in the CLI.

Background: main.py dispatches KNOWN CLI verbs straight to cli.main so the
container entrypoint supports subcommands. The gate is is_known_verb() +
the argv shape check in main.main() — a regression here either breaks
`docker run image <verb>` (verb missing from the frozenset) or makes every
pytest process explode with "invalid choice" (gate too permissive).
"""

import re
import sys
from unittest import mock

import pytest

from suijin.modules.console.lib import cli


class TestIsKnownVerb:
    def test_truth_table(self):
        assert cli.is_known_verb("version")
        assert cli.is_known_verb("doctor")
        assert cli.is_known_verb(" doctor ")  # stripped — stray whitespace is not a miss
        assert not cli.is_known_verb("notaverb")
        assert not cli.is_known_verb("")
        assert not cli.is_known_verb(None)
        assert not cli.is_known_verb("Version")  # case-sensitive, as argparse is

    def test_frozenset_matches_real_cli_choices(self, capsys):
        """The single source of truth is the argparse subparser set — the
        frozenset must track it exactly (new verb added to cli.main but not
        _KNOWN_VERBS = broken container dispatch)."""
        with pytest.raises(SystemExit) as ei:
            cli.main(["____probe_verb____"])  # invalid choice error lists every choice
        assert ei.value.code == 2
        err = capsys.readouterr().err
        m = re.search(r"invalid choice: '____probe_verb____' \(choose from (.+)\)", err)
        assert m, f"argparse error did not enumerate choices: {err!r}"
        choices = {c.strip().strip("'") for c in m.group(1).split(",")}
        known = set(cli._KNOWN_VERBS)
        assert choices == known, (
            f"verb drift: only-in-cli={sorted(choices - known)} stale-in-frozenset={sorted(known - choices)}"
        )


class TestMainDispatchGate:
    def test_known_verb_dispatches_to_cli(self, monkeypatch):
        import suijin.main as m

        seen = {}

        def _fake_cli_main(a):
            seen["argv"] = a
            return 0

        monkeypatch.setattr(sys, "argv", ["suijin", "version"])
        monkeypatch.setattr(cli, "main", _fake_cli_main)
        with pytest.raises(SystemExit) as ei:
            m.main()
        assert seen["argv"] == ["version"]
        assert ei.value.code == 0

    @pytest.mark.parametrize("argv1", ["tests/console/test_ctrlc.py", "notaverb", "--flag"])
    def test_non_verb_argv_never_dispatches(self, monkeypatch, argv1):
        """pytest's argv (a .py path), unknown words, and flags all fall
        through to the interactive TUI path — never to the CLI."""
        import suijin.main as m

        class Sentinel(Exception):
            pass

        def _boom(*a):
            raise AssertionError(f"CLI must not be invoked for argv[0]={argv1!r}")

        monkeypatch.setattr(sys, "argv", ["suijin", argv1])
        monkeypatch.setattr(cli, "main", _boom)
        # abort the TUI path right after the gate proves dispatch was skipped
        monkeypatch.setattr(
            "suijin.modules.platform.lib.runtime.init_runtime",
            mock.Mock(side_effect=Sentinel("reached TUI path — correct")),
        )
        with pytest.raises(Sentinel):
            m.main()
