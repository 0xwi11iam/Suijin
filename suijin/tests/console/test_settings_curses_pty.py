"""The settings editor driven the way a HUMAN drives it — on a real
curses terminal in a pty.

These are keystroke sequences, not unit calls. Each one presses the keys a
person presses and then asserts the config file actually changed, because
that is the only thing the operator cares about: "I clicked it and it
failed" is a failure even when every helper function works.

Covered (all found broken at least once by this harness):
  - Enter on a field opens it; the provider picker used to pre-fill the
    buffer with the current value, so arrowing + Enter changed nothing
  - typing filters 55 providers down to one ("openc" → opencode)
  - a bool flips on Enter (it used to open a prompt where Enter = cancel)
  - numbers clamp to their bounds
  - `m` picks from the provider's LIVE model ids
  - Esc cancels without changing anything
"""

from __future__ import annotations

import curses
import json
import os
import pty
import select
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KEY_DOWN, KEY_UP, ENTER, ESC = curses.KEY_DOWN, curses.KEY_UP, 13, 27


def _drive(workspace: Path, config: dict, keys: list[int], tag: str, rows: int = 30, cols: int = 100) -> dict:
    """Spawn the real editor in a pty, feed the keys, return the saved config."""
    cfg_path = workspace / f"{tag}.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")
    child = f"""
import os, sys, traceback
sys.path.insert(0, {str(REPO)!r})
import curses
import suijin.modules.console.lib.settings_tui as st
st.CONFIG_PATH = {str(cfg_path)!r}
SCRIPT = {list(keys)!r}
n = [0]
class Proxy:
    def __init__(self, s): object.__setattr__(self, "_s", s)
    def __getattr__(self, k): return getattr(object.__getattribute__(self, "_s"), k)
    def getch(self):
        n[0] += 1
        if n[0] > 80: os._exit(3)
        return SCRIPT.pop(0) if SCRIPT else {ESC}
def loop(scr):
    st._init_colors(scr)
    return st._curses_loop(Proxy(scr))
try:
    curses.wrapper(loop)
    open(os.environ["OUT"], "w").write("ok")
except Exception:
    open(os.environ["OUT"], "w").write(traceback.format_exc()[-300:])
"""
    out = workspace / f"{tag}.out"
    env = dict(os.environ, OUT=str(out), TERM="xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.execve(sys.executable, [sys.executable, "-c", child], env)
    deadline = time.time() + 90
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if ready:
            try:
                if not os.read(fd, 65536):
                    break
            except OSError:
                break
        if out.exists():
            break
    os.waitpid(pid, 0)
    assert out.exists(), "the editor hung (no exit)"
    report = out.read_text()
    assert report == "ok", f"the real screen failed: {report[:500]}"
    return json.loads(cfg_path.read_text())


@pytest.fixture()
def base():
    return {"provider": "zai", "zai_model": "glm-5.3", "max_iterations": 100000}


@pytest.fixture()
def row_of():
    from suijin.modules.console.lib.settings_tui import _row_items, _visible_fields

    def _row_of(key: str, config: dict) -> int:
        return [k for _g, k in _row_items(_visible_fields(config))].index(key)

    return _row_of


class TestAsAHuman:
    def test_enter_then_type_switches_the_provider(self, tmp_path, base):
        """The report: "I press enter on providers and it doesn't work"."""
        out = _drive(tmp_path, base, [ENTER] + [ord(c) for c in "openc"] + [ENTER, ord("q")], "provider")
        assert out["provider"] == "opencode"

    def test_arrows_then_enter_switches_the_provider(self, tmp_path, base):
        """Arrowing must actually move the commit target (it used not to:
        the pre-filled buffer won over the highlight)."""
        from suijin.modules.console.lib.settings_tui import provider_choices

        choices = provider_choices(base)
        before = len(choices) - 1 - choices.index("zai")  # how far up to reach the top
        out = _drive(tmp_path, base, [ENTER] + [KEY_UP] * before + [ENTER, ord("q")], "arrow")
        assert out["provider"] == choices[0]

    def test_bool_flips_on_enter(self, tmp_path, base, row_of):
        """A checkbox: Enter flips it. (It used to open a prompt where
        Enter meant cancel — so no bool could ever be changed.)"""
        base = dict(base, mode_hitl=False)
        out = _drive(tmp_path, base, [KEY_DOWN] * row_of("mode_hitl", base) + [ENTER, ord("q")], "bool")
        assert out["mode_hitl"] is True

    def test_number_typing_and_clamping(self, tmp_path, base, row_of):
        base = dict(base, max_iterations=100)
        out = _drive(
            tmp_path,
            base,
            [KEY_DOWN] * row_of("max_iterations", base) + [ENTER] + [ord(c) for c in "250"] + [ENTER, ord("q")],
            "num",
        )
        assert out["max_iterations"] == 250
        # out of bounds clamps rather than storing nonsense
        out2 = _drive(
            tmp_path,
            base,
            [KEY_DOWN] * row_of("max_iterations", base) + [ENTER] + [ord(c) for c in "99999999"] + [ENTER, ord("q")],
            "clamp",
        )
        assert out2["max_iterations"] == 1_000_000

    def test_esc_cancels_without_changing_anything(self, tmp_path, base):
        out = _drive(tmp_path, base, [ENTER] + [ord(c) for c in "WRECK"] + [ESC, ord("q")], "esc")
        assert out["provider"] == "zai"

    def test_free_text_value(self, tmp_path, base, row_of):
        out = _drive(
            tmp_path,
            base,
            [KEY_DOWN] * row_of("proxy_url", base)
            + [ENTER]
            + [ord(c) for c in "http://127.0.0.1:8080"]
            + [ENTER, ord("q")],
            "free",
        )
        assert out["proxy_url"] == "http://127.0.0.1:8080"

    def test_navigating_around_changes_nothing(self, tmp_path, base):
        out = _drive(tmp_path, base, [KEY_DOWN, KEY_UP, KEY_DOWN, ord("q")], "nav")
        assert out["provider"] == "zai" and out["max_iterations"] == 100000

    @pytest.mark.ai
    def test_m_picks_from_the_live_model_list(self, tmp_path, base, row_of):
        """`m` fetches the provider's real model ids and picks one (live)."""
        from suijin.modules.console.lib.settings_tui import fetch_model_ids

        ids, note = fetch_model_ids("zai", base)
        if not ids:
            pytest.skip(f"live fetch unavailable: {note}")
        target = next((i for i in ids if i != "glm-5.3"), ids[0])
        out = _drive(
            tmp_path,
            base,
            [KEY_DOWN] * row_of("zai_model", base) + [ord("m")] + [ord(c) for c in target] + [ENTER, ord("q")],
            "models",
        )
        assert out["zai_model"] == target


class TestApiKeyRow:
    """The API key row: masked, stored in .env, never in config.json.

    A secret must not land in config.json — that file is snapshotted into
    .sje bundles and read by other tools. The env var name comes from the
    provider layer (registry spec or the bespoke path), so a new provider
    needs no edit here.
    """

    def _drive_with_env(self, tmp_path, keys, provider="opencode", tag="key"):
        """The pty driver plus a redirected .env (so the test never touches
        the operator's real key file)."""

        env_path = tmp_path / f"{tag}.env"
        env_path.write_text("", encoding="utf-8")
        old_drive = _drive

        def _patched(workspace, config, k, name, rows=30, cols=100):
            # the child re-points config_loader.ENV_PATH at our temp file
            old_drive(workspace, config, k, name, rows, cols)

        return _patched, env_path

    def test_api_key_env_is_resolved_by_the_provider_layer(self):
        """Nothing is hardcoded: registry providers answer from their spec,
        bespoke ones from the layer's own env lookups."""
        from suijin.modules.providers.lib import provider_key_env

        assert provider_key_env("opencode") == "OPENCODE_API_KEY"  # a registry row
        assert provider_key_env("openrouter") == "OPENROUTER_API_KEY"
        assert provider_key_env("zai") == "ZAI_API_KEY"  # a bespoke path
        assert provider_key_env("ollama") == ""  # local needs none
        assert provider_key_env("") == ""

    def test_key_row_appears_under_the_model_and_is_masked(self, monkeypatch, tmp_path):
        import suijin.modules.console.lib.settings_tui as st
        from suijin.modules.providers.lib import registry  # noqa: F401

        monkeypatch.setattr(st, "CONFIG_PATH", str(tmp_path / "config.json"))
        (tmp_path / "config.json").write_text(json.dumps({"provider": "opencode", "opencode_model": "glm-5.3"}))
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-live-secret-value")
        monkeypatch.setattr("suijin.modules.platform.lib.config_loader.load_env", lambda: None, raising=False)
        config = st.load_config()
        lines = [t for t, _r in st.screen_lines(config, cursor=0)]
        key_rows = [t for t in lines if "api_key" in t]
        assert key_rows, "no api_key row under the model"
        # the VALUE is never on screen — only that a key is on file
        assert "sk-live-secret-value" not in "\n".join(lines)
        assert "••••" in key_rows[0]
        # and the row is reachable by the cursor, right under the model
        items = [k for _g, k in st._row_items(st._visible_fields(config))]
        assert items[items.index("opencode_model") + 1] == st.API_KEY_ROW

    def test_set_provider_key_writes_env_not_config(self, monkeypatch, tmp_path):

        from suijin.modules.platform.lib import config_loader as cl
        from suijin.modules.providers.lib import get_provider_key, set_provider_key

        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=1\nOPENCODE_API_KEY=old-key\n", encoding="utf-8")
        monkeypatch.setattr(cl, "ENV_PATH", env_path)
        # set_provider_key DOES set os.environ (that is the point: the live
        # process sees the key) — so the test must hand the env back or it
        # leaks an OPENCODE key into every later test (a keyless auto-chain
        # test started failing because of it).
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-new-secret")
        ok, message = set_provider_key("opencode", "sk-new-secret")
        assert ok, message
        text = env_path.read_text()
        assert "OPENCODE_API_KEY=sk-new-secret" in text  # replaced in place
        assert "old-key" not in text
        assert "EXISTING=1" in text  # other keys untouched
        # readable live, and never returned for display
        assert get_provider_key("opencode") == "sk-new-secret"

    def test_set_key_refuses_when_there_is_no_env_to_hold_it(self):
        from suijin.modules.providers.lib import set_provider_key

        ok, message = set_provider_key("ollama", "x")
        assert not ok and "no API key" in message

    def test_secret_is_never_echoed(self):
        """The prompt masks as you type — the secret never hits the screen."""
        import inspect

        from suijin.modules.console.lib.settings_tui import _prompt

        src = inspect.getsource(_prompt)
        assert "secret" in src
        assert '"*" * len(typed)' in src  # masked per character
        assert "*" in src and "typed" in src


def test_human_enters_an_api_key_hidden(tmp_path, monkeypatch):
    """The full human path: arrow to the key row, Enter, type (hidden),
    Enter, quit — the secret ends up in .env and NEVER in config.json."""
    import os
    import pty as _pty
    import select as _select
    import sys as _sys
    import time as _time

    secret = "sk-test-SECRET-abc123"
    cfg_path = tmp_path / "keyrun.json"
    env_path = tmp_path / "keyrun.env"
    cfg_path.write_text(json.dumps({"provider": "opencode", "opencode_model": "glm-5.3"}), encoding="utf-8")
    env_path.write_text("", encoding="utf-8")
    child = f"""
import os, sys, json, traceback
from pathlib import Path
sys.path.insert(0, {str(REPO)!r})
import curses
import suijin.modules.console.lib.settings_tui as st
st.CONFIG_PATH = {str(cfg_path)!r}
from suijin.modules.platform.lib import config_loader as cl
cl.ENV_PATH = Path({str(env_path)!r})
# down to the model, down to the key row, Enter, type, Enter, quit
SCRIPT = [curses.KEY_DOWN, curses.KEY_DOWN, 13] + [ord(c) for c in {secret!r}] + [13, ord("q")]
n = [0]
class Proxy:
    def __init__(self, s): object.__setattr__(self, "_s", s)
    def __getattr__(self, k): return getattr(object.__getattribute__(self, "_s"), k)
    def getch(self):
        n[0] += 1
        if n[0] > 90: os._exit(3)
        return SCRIPT.pop(0) if SCRIPT else 27
def loop(scr):
    st._init_colors(scr)
    return st._curses_loop(Proxy(scr))
try:
    curses.wrapper(loop)
    open(os.environ["OUT"], "w").write("ok")
except Exception:
    open(os.environ["OUT"], "w").write(traceback.format_exc()[-300:])
"""
    out = tmp_path / "keyrun.out"
    env = dict(os.environ, OUT=str(out), TERM="xterm-256color")
    pid, fd = _pty.fork()
    if pid == 0:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        os.execve(_sys.executable, [_sys.executable, "-c", child], env)
    deadline = _time.time() + 60
    while _time.time() < deadline:
        ready, _, _ = _select.select([fd], [], [], 0.3)
        if ready:
            try:
                if not os.read(fd, 65536):
                    break
            except OSError:
                break
        if out.exists():
            break
    os.waitpid(pid, 0)
    assert out.exists() and out.read_text() == "ok", (
        f"editor failed: {out.read_text()[:300] if out.exists() else 'hung'}"
    )
    env_text = env_path.read_text()
    cfg_text = cfg_path.read_text()
    assert f"OPENCODE_API_KEY={secret}" in env_text, "the key did not reach .env"
    assert secret not in cfg_text, "the secret leaked into config.json"
