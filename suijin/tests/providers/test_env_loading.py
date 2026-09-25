"""load_env() must parse a .env WITHOUT ever touching the operator's real
secrets file.

This was a real leak: a test called load_env() on the live .env, which
inserted real API keys into os.environ with no teardown, so those secrets
leaked into every later test in the session and into any child process it
spawned. The suite now runs against a sandbox .env (see tests/conftest.py)
and this file proves the parser itself is correct and contained.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """Point load_env() at a synthetic file and guarantee the parsed
    variables are removed again afterwards.

    load_env() is defined in config_loader and reads THAT module's
    ENV_PATH — redteamer only re-exports the function, so patching a
    re-exported constant has no effect (which is exactly why the old
    test could not sandbox it and hit the real .env).
    """
    from suijin.modules.platform.lib import config_loader

    path = tmp_path / ".env"
    monkeypatch.setattr(config_loader, "ENV_PATH", path)
    return path


def test_parses_plain_quoted_and_exported_entries(env_file):
    from suijin.modules.platform.lib.config_loader import load_env

    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "PLAIN=abc123",
                "QUOTED='a b'",
                'DQUOTED="c d"',
                "export EXPORTED=yes",
                "EMPTY=",
                "  SPACED = trimmed  ",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for name in ("PLAIN", "QUOTED", "DQUOTED", "EXPORTED", "EMPTY", "SPACED"):
        os.environ.pop(name, None)
    try:
        load_env()
        assert os.environ["PLAIN"] == "abc123"
        assert os.environ["QUOTED"] == "a b"
        assert os.environ["DQUOTED"] == "c d"
        assert os.environ["EXPORTED"] == "yes"
        assert os.environ["EMPTY"] == ""
        assert os.environ["SPACED"] == "trimmed"
    finally:
        for name in ("PLAIN", "QUOTED", "DQUOTED", "EXPORTED", "EMPTY", "SPACED"):
            os.environ.pop(name, None)


def test_the_env_file_wins_over_an_inherited_shell_value(env_file):
    """.env is the authoritative key store for this project — a value in
    the file beats one inherited from the shell.

    This is deliberate and the opposite of python-dotenv's default. Here
    .env IS the settings surface ("cloud rows activate on key presence in
    .env"), so if a stale shell variable silently won, an operator who
    edited their key and restarted would keep authenticating with the
    OLD one and get an inexplicable 401/402 instead of a clear "no key
    configured". File wins is the predictable rule.
    """
    from suijin.modules.platform.lib.config_loader import load_env

    env_file.write_text("SUIJIN_ENV_WINS=from-file\n", encoding="utf-8")
    os.environ["SUIJIN_ENV_WINS"] = "from-shell"
    try:
        load_env()
        assert os.environ["SUIJIN_ENV_WINS"] == "from-file"
    finally:
        os.environ.pop("SUIJIN_ENV_WINS", None)


def test_absent_file_is_not_an_error(tmp_path, monkeypatch):
    from suijin.modules.platform.lib import config_loader

    monkeypatch.setattr(config_loader, "ENV_PATH", tmp_path / "nope.env")
    config_loader.load_env()  # parses nothing, raises nothing


def test_malformed_file_does_not_crash(tmp_path, monkeypatch):
    from suijin.modules.platform.lib import config_loader

    bad = tmp_path / ".env"
    bad.write_text("=novalue\njust words\nA=B=C\n", encoding="utf-8")
    monkeypatch.setattr(config_loader, "ENV_PATH", bad)
    config_loader.load_env()  # must never raise into boot


class TestFieldBugs:
    """Three real defects, each of which reached boot."""

    def test_a_line_starting_with_equals_does_not_crash_boot(self, env_file):
        """`=novalue` parsed to an EMPTY name and os.environ[...] raised
        OSError(EINVAL), which propagated out of load_env() and killed the
        app on startup. A malformed dotfile must never be fatal."""
        from suijin.modules.platform.lib.config_loader import load_env

        env_file.write_text("=novalue\nZAI_API_KEY=real\n", encoding="utf-8")
        os.environ.pop("ZAI_API_KEY", None)
        try:
            load_env()  # used to raise OSError here
            assert os.environ["ZAI_API_KEY"] == "real"  # ...and still parsed the rest
        finally:
            os.environ.pop("ZAI_API_KEY", None)

    def test_export_prefix_is_not_part_of_the_key(self, env_file):
        """`export FOO=bar` used to create a variable literally named
        "export FOO", so the provider layer never saw FOO at all."""
        from suijin.modules.platform.lib.config_loader import load_env

        env_file.write_text("export ZAI_API_KEY=from-export\n", encoding="utf-8")
        os.environ.pop("ZAI_API_KEY", None)
        os.environ.pop("export ZAI_API_KEY", None)
        try:
            load_env()
            assert os.environ["ZAI_API_KEY"] == "from-export"
            assert "export ZAI_API_KEY" not in os.environ
        finally:
            os.environ.pop("ZAI_API_KEY", None)
            os.environ.pop("export ZAI_API_KEY", None)

    def test_quotes_are_stripped_so_the_key_is_usable(self, env_file):
        """`FOO='bar'` used to yield the literal `'bar'` — a broken key
        that presents as an unexplained 401 rather than a clear failure."""
        from suijin.modules.platform.lib.config_loader import load_env

        env_file.write_text("ZAI_API_KEY='quoted-key'\nHF_TOKEN=\"hf-1\"\n", encoding="utf-8")
        try:
            load_env()
            assert os.environ["ZAI_API_KEY"] == "quoted-key"
            assert os.environ["HF_TOKEN"] == "hf-1"
        finally:
            os.environ.pop("ZAI_API_KEY", None)
            os.environ.pop("HF_TOKEN", None)

    def test_a_comment_containing_equals_is_not_a_variable(self, env_file):
        from suijin.modules.platform.lib.config_loader import load_env

        env_file.write_text("# docs say use ?a=b for that flag\nA_REAL=1\n", encoding="utf-8")
        try:
            load_env()
            assert os.environ["A_REAL"] == "1"
            assert not [k for k in os.environ if k.startswith("#")]
        finally:
            os.environ.pop("A_REAL", None)


class TestSettingsKeyRoundTrip:
    """A key typed in Settings must come back out of .env exactly as it
    was usable. set_provider_key writes, load_env reads; if they disagree
    about quoting the mismatch surfaces only later as a 401.
    """

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("sk-plain", "sk-plain"),
            ('"sk-pasted"', "sk-pasted"),
            ("'sk-pasted'", "sk-pasted"),
            ("  sk-spaced  ", "sk-spaced"),
            ("sk-with=equals", "sk-with=equals"),
            ("sk;semi&pipe", "sk;semi&pipe"),
        ],
    )
    def test_write_then_read_is_lossless(self, env_file, typed, expected):
        import os as _os

        from suijin.modules.providers.lib import set_provider_key

        _os.environ.pop("OPENCODE_API_KEY", None)
        try:
            ok, _msg = set_provider_key("opencode", typed)
            assert ok
            load_env = __import__("suijin.modules.platform.lib.config_loader", fromlist=["load_env"]).load_env
            load_env()
            assert _os.environ["OPENCODE_API_KEY"] == expected
        finally:
            _os.environ.pop("OPENCODE_API_KEY", None)
