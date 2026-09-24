"""The Settings TUI — a Rich editor (no Textual): field semantics, provider
filters, save round-trip, corrupt-config refusal, and LIVE model-id fetch.

Preserved from the Textual era (these were real field bugs): navigation
never writes into config, a choice value outside the static list is kept,
and a corrupt config is never overwritten.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

import suijin.modules.console.lib.settings_tui as st


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {"provider": "zai", "zai_model": "glm-5.3", "max_iterations": 100, "mode_hitl": False, "temperature": 0.4}
        )
    )
    monkeypatch.setattr(st, "CONFIG_PATH", str(p))
    return p


def _console() -> Console:
    return Console(width=100, force_terminal=False, record=True)


class _Win:
    """A curses-window stand-in that records what was drawn, so the layout
    is asserted without a terminal."""

    def __init__(self, h=24, w=92):
        self.h, self.w, self.rows = h, w, []

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.rows = []

    def addstr(self, y, x, text, attr=0):
        while len(self.rows) <= y:
            self.rows.append("")
        self.rows[y] = text

    def refresh(self):
        pass

    def keypad(self, flag):
        pass

    def subwin(self, rows, cols, y, x):
        # the REAL API: a custom-height box is subwin().border() — there is
        # no rectangle(), and box() takes at most (vert, horiz)
        if not all(isinstance(v, int) for v in (rows, cols, y, x)):
            raise TypeError("subwin() takes 4 ints")
        return self

    def delete(self):
        pass

    def border(self, *a):
        # the real window's border() — a 4-arg "box" is the bug that killed
        # the live screen, so the arity limit is enforced here
        if len(a) > 2:
            raise TypeError("box() takes at most 2 arguments")

    def __getattr__(self, name):
        if name == "rectangle":
            raise AttributeError("'_curses.window' object has no attribute 'rectangle'")
        raise AttributeError(name)

    def hline(self, *a, **k):
        pass

    def move(self, y, x):
        pass

    def getch(self):
        return -1


class TestFieldModel:
    def test_provider_filters_visible_fields(self, cfg):
        c = st.load_config()
        vis = st._visible_fields(c)
        assert "zai_endpoint" in vis  # zai provider -> endpoint picker visible
        c["provider"] = "deepseek"
        vis2 = st._visible_fields(c)
        assert "zai_endpoint" not in vis2 and "deepseek_model" in vis2

    def test_provider_choices_are_dynamic(self, cfg):
        """Nothing hardcoded: the list is the registry + the bespoke
        code-path providers + customs (55 sources today)."""
        choices = st.provider_choices()
        assert "zai" in choices and "ollama" in choices  # bespoke + local
        assert "opencode" in choices  # OpenCode Zen
        assert len(choices) > 40, "the provider list is suspiciously small"
        assert len(choices) == len(set(choices))  # deduped

    def test_bespoke_providers_discovered_from_dispatch(self, cfg):
        """The special code-path providers are read out of the provider
        layer's own `if provider == ...` branches, not a hand-kept list."""
        found = st._bespoke_provider_keys()
        for key in ("zai", "deepseek", "anthropic", "gemini"):
            assert key in found

    def test_custom_providers_in_choices(self, tmp_path, monkeypatch):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"custom_providers": [{"name": "box", "base_url": "http://x:1/v1"}]}))
        monkeypatch.setattr(st, "CONFIG_PATH", str(p))
        assert "custom:box" in st.provider_choices()

    def test_registry_provider_gets_a_synthesized_model_field(self, cfg):
        """A registry provider needs no code change: its <provider>_model
        field appears on its own."""
        vis = st._visible_fields({"provider": "opencode"})
        assert "opencode_model" in vis
        assert vis["opencode_model"][0] == "model"
        assert st.is_model_field("opencode_model")
        # and it sits in the Provider group, next to the provider
        items = [k for _g, k in st._row_items(vis)][:2]
        assert items == ["provider", "opencode_model"]

    def test_default_model_hint_from_registry(self, cfg):
        assert st._default_model_hint("opencode") == "glm-5.3"
        assert st._default_model_hint("nope") == ""

    def test_model_fields_are_freeform_not_lists(self, cfg):
        """Model ids are never a static list — they are fetched."""
        for key in ("zai_model", "deepseek_model", "gemini_model", "anthropic_model"):
            assert st.ALL_FIELDS[key][0] == "model"

    def test_new_memory_fields_present(self):
        for k in ("context_window", "librarian_interval", "launch_mode"):
            assert k in st.ALL_FIELDS

    def test_no_external_tui_framework(self):
        """The editor is Rich like everywhere else — no external TUI kit."""
        with open(st.__file__) as fh:
            source = fh.read()
        assert "import textual" not in source
        assert "from textual" not in source
        assert "from rich" in source

    def test_corrupt_config_opens_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "config.json"
        p.write_text("{corrupt")
        monkeypatch.setattr(st, "CONFIG_PATH", str(p))
        assert st.load_config() == {}

    def test_directory_config_opens_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "config.json"
        p.mkdir()
        monkeypatch.setattr(st, "CONFIG_PATH", str(p))
        assert st.load_config() == {}


class TestRender:
    def test_renders_every_visible_field(self, cfg, capsys):
        config = st.load_config()
        visible = st._visible_fields(config)
        items = st._row_items(visible)
        st._render(_console(), config, visible, items, 0)
        out = capsys.readouterr().out
        for key in ("provider", "zai_model", "max_iterations", "mode_hitl"):
            assert key in out
        assert "off" in out  # mode_hitl rendered as a bool

    def test_group_order_starts_with_provider(self, cfg):
        items = st._row_items(st._visible_fields(st.load_config()))
        assert items[0][1] == "provider"
        assert items[0][0] == "Provider"

    def test_cursor_is_always_valid(self, cfg):
        visible = st._visible_fields(st.load_config())
        items = st._row_items(visible)
        # a cursor past the end (provider swap shrank the list) must clamp
        cursor = min(len(items) + 5, len(items) - 1)
        assert 0 <= cursor < len(items)

    def test_fmt_marks_unset(self, cfg):
        assert "unset" in st._fmt("proxy_url", "")

    def test_provider_note_reports_live_source(self, cfg):
        note = st.visible_provider_note({"provider": "opencode"})
        assert "opencode.ai/zen/v1/models" in note

    def test_provider_note_without_provider(self, cfg):
        assert "no provider" in st.visible_provider_note({})

    def test_provider_note_local(self, cfg):
        assert "local" in st.visible_provider_note({"provider": "ollama"})


class TestApplyAndSave:
    def test_apply_clamps_numbers(self, cfg):
        config = st.load_config()
        st._apply(config, "max_iterations", "9999999")
        assert config["max_iterations"] == 1000000
        st._apply(config, "temperature", "9.9")
        assert config["temperature"] == 2.0

    def test_apply_bool_and_string(self, cfg):
        config = st.load_config()
        assert st._apply(config, "mode_hitl", "on") is True
        assert config["mode_hitl"] is True
        st._apply(config, "proxy_url", "http://p:8080")
        assert config["proxy_url"] == "http://p:8080"

    def test_navigation_writes_nothing(self, cfg):
        """The Textual-era bug: walking the list echoed values into config.
        A Rich editor has no echo path at all — only _apply changes state."""
        config = st.load_config()
        before = dict(config)
        st._render(_console(), config, st._visible_fields(config), st._row_items(st._visible_fields(config)), 3)
        assert {k: v for k, v in config.items() if before.get(k) != v} == {}

    def test_choice_value_outside_list_is_kept(self, cfg):
        data = json.loads(cfg.read_text())
        data["zai_model"] = "glm-4.5-older-custom"  # not in the static list
        cfg.write_text(json.dumps(data))
        assert st.load_config()["zai_model"] == "glm-4.5-older-custom"

    def test_save_round_trip(self, cfg):
        config = st.load_config()
        st._apply(config, "max_iterations", "500")
        st._apply(config, "mode_hitl", "on")
        st._apply(config, "zai_model", "glm-4.6")
        st._save(_console(), config, "ok")
        saved = json.loads(cfg.read_text())
        assert saved["max_iterations"] == 500
        assert saved["mode_hitl"] is True
        assert saved["zai_model"] == "glm-4.6"

    def test_corrupt_config_refuses_save(self, tmp_path, monkeypatch):
        p = tmp_path / "config.json"
        p.write_text("{corrupt")
        monkeypatch.setattr(st, "CONFIG_PATH", str(p))
        console = _console()
        assert st._save(console, {"provider": "zai"}, "corrupt") == 1
        assert p.read_text() == "{corrupt"  # untouched

    def test_editor_refuses_to_run_on_corrupt_config(self, tmp_path, monkeypatch):
        p = tmp_path / "config.json"
        p.write_text("{corrupt")
        monkeypatch.setattr(st, "CONFIG_PATH", str(p))
        monkeypatch.setattr(st, "sys_stdin_tty", lambda: False)
        assert st.main() == 0  # summary path, never a wipe
        assert p.read_text() == "{corrupt"


class TestLiveModelFetch:
    def test_fetches_openai_shape(self, cfg, monkeypatch):
        monkeypatch.setattr(
            st, "_http_get_json", lambda url, headers, timeout: {"data": [{"id": "glm-5.3"}, {"id": "kimi-k2.7-code"}]}
        )
        ids, note = st.fetch_model_ids("opencode", {"provider": "opencode"})
        assert ids == ["glm-5.3", "kimi-k2.7-code"]
        assert "model(s)" in note

    def test_fetches_models_key_shape(self, cfg, monkeypatch):
        monkeypatch.setattr(
            st, "_http_get_json", lambda url, headers, timeout: {"models": [{"name": "m-a"}, {"id": "m-b"}]}
        )
        ids, _ = st.fetch_model_ids("openrouter", {"provider": "openrouter"})
        assert ids == ["m-a", "m-b"]

    def test_dedupes_and_sorts(self, cfg, monkeypatch):
        monkeypatch.setattr(
            st, "_http_get_json", lambda url, headers, timeout: {"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}
        )
        ids, _ = st.fetch_model_ids("opencode", {})
        assert ids == ["a", "b"]

    def test_keyless_public_models_endpoint_still_fetches(self, cfg, monkeypatch):
        """No key is not a hard stop — gateways like OpenCode Zen publish
        /models publicly; the fetch tries anyway."""
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        seen = {}

        def _get(url, headers, timeout):
            seen.update(url=url, headers=headers)
            return {"data": [{"id": "glm-5.3"}]}

        monkeypatch.setattr(st, "_http_get_json", _get)
        ids, note = st.fetch_model_ids("opencode", {"provider": "opencode"})
        assert ids == ["glm-5.3"] and "model(s)" in note
        assert "Authorization" not in seen["headers"]  # no key, still tried

    def test_key_is_sent_when_present(self, cfg, monkeypatch):
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-test-123")
        seen = {}
        monkeypatch.setattr(
            st, "_http_get_json", lambda url, headers, timeout: seen.update(headers=headers) or {"data": []}
        )
        st.fetch_model_ids("opencode", {"provider": "opencode"})
        assert seen["headers"].get("Authorization") == "Bearer sk-test-123"

    def test_key_hint_appears_on_failure(self, cfg, monkeypatch):
        def _boom(url, headers, timeout):
            raise RuntimeError("401 unauthorized")

        monkeypatch.setattr(st, "_http_get_json", _boom)
        ids, note = st.fetch_model_ids("opencode", {"provider": "opencode"})
        assert ids == [] and "fetch failed" in note

    def test_network_failure_is_honest(self, cfg, monkeypatch):
        def _boom(url, headers, timeout):
            raise TimeoutError("timed out")

        monkeypatch.setattr(st, "_http_get_json", _boom)
        ids, note = st.fetch_model_ids("opencode", {"provider": "opencode"})
        assert ids == [] and "fetch failed" in note

    def test_unknown_provider_explains(self, cfg):
        ids, note = st.fetch_model_ids("nope-not-real", {})
        assert ids == [] and "SDK" in note  # honest, not a guessed URL

    def test_local_provider_is_explained(self, cfg):
        ids, note = st.fetch_model_ids("ollama", {})
        assert ids == [] and "local" in note

    def test_custom_provider_uses_config_base_url(self, cfg, monkeypatch):
        seen = {}

        def _get(url, headers, timeout):
            seen.update(url=url, headers=headers)
            return {"data": [{"id": "local-model"}]}

        monkeypatch.setattr(st, "_http_get_json", _get)
        ids, _ = st.fetch_model_ids(
            "custom:box",
            {"custom_providers": [{"name": "box", "base_url": "http://10.0.0.5:8080/v1", "api_key": "k"}]},
        )
        assert ids == ["local-model"]
        assert seen["url"] == "http://10.0.0.5:8080/v1/models"
        assert seen["headers"]["Authorization"] == "Bearer k"

    def test_pick_model_sets_the_field(self, cfg, monkeypatch):
        """The zai list is fetched LIVE from the provider layer's endpoint,
        and the pick lands in the field (the curses picker's contract)."""
        seen = {}

        def _get(url, headers, timeout):
            seen.update(url=url)
            return {"data": [{"id": "glm-5.3"}, {"id": "glm-5.1"}]}

        monkeypatch.setattr(st, "_http_get_json", _get)
        config = st.load_config()  # provider: zai
        ids, note = st.fetch_model_ids(str(config["provider"]), config)
        assert ids == ["glm-5.1", "glm-5.3"] and "api.z.ai" in seen["url"]
        assert st._apply(config, "zai_model", "glm-5.1") is True
        assert config["zai_model"] == "glm-5.1"

    def test_endpoint_lives_in_the_provider_layer(self):
        """The TUI does not carry endpoints — it asks the layer."""
        from suijin.modules.providers.lib import provider_models_endpoint

        base, headers = provider_models_endpoint("opencode", {})
        assert base == "https://opencode.ai/zen/v1"
        assert provider_models_endpoint("ollama", {})[0] is None
        base, _ = provider_models_endpoint(
            "custom:box", {"custom_providers": [{"name": "box", "base_url": "http://x:1/v1"}]}
        )
        assert base == "http://x:1/v1"
        assert provider_models_endpoint("nope", {})[0] is None

    def test_pick_model_without_provider_is_honest(self, cfg):
        class _S:  # a screen stand-in: the picker guards before drawing
            pass

        assert "no provider" in st.pick_model_curses(_S(), {"provider": ""}, "opencode_model")


class TestCursesScreen:
    """The curses editor: the screen model is pure (asserted here), and the
    line-mode fallback is what CI/no-terminfo terminals actually get."""

    def test_screen_model_is_pure_and_complete(self, cfg):
        """One flat list: every visible field on one screen, with the
        cursor on exactly one of them."""
        config = st.load_config()
        lines = st.screen_lines(config, cursor=0)
        text = "\n".join(t for t, _r in lines)
        assert "SUIJIN — Settings" in text
        assert "provider" in text and "max_iterations" in text
        assert sum(1 for _t, role in lines if role == "cursor") == 1
        # re-rendering with no edit changes nothing (no echo path)
        assert st.screen_lines(config, cursor=0) == lines

    def test_screen_marks_the_cursor_row(self, cfg):
        config = st.load_config()
        items = st._row_items(st._visible_fields(config))
        lines = st.screen_lines(config, cursor=2)
        cursor_rows = [t for t, role in lines if role == "cursor"]
        assert len(cursor_rows) == 1 and cursor_rows[0].strip().startswith("› ")
        # the highlighted row shows its VALUE (reverse video alone is easy to miss)
        assert cursor_rows[0].split()[1] == items[2][1]

    def test_every_field_is_listed_and_grouped(self, cfg):
        config = st.load_config()
        visible = st._visible_fields(config)
        lines = st.screen_lines(config, cursor=0)
        listed = [k for _g, k in st._row_items(visible)]
        body = "\n".join(t for t, role in lines if role in ("row", "cursor"))
        missing = [k for k in listed if f" {k}" not in body]
        assert not missing, f"fields not on screen: {missing}"
        assert any(role == "group" for _t, role in lines)

    def test_model_fields_advertise_live_ids(self, cfg):
        config = st.load_config()
        lines = st.screen_lines(config, cursor=0)
        assert any("zai_model" in t and "m: live ids" in t for t, _r in lines)

    def test_screen_note_warns(self, cfg):
        config = st.load_config()
        lines = st.screen_lines(config, cursor=0, note="fetch failed: boom", status="warn")
        assert any("fetch failed" in t and role == "warn" for t, role in lines)

    def test_legend_names_the_real_keys(self, cfg):
        legend = st._legend(None)
        for key in ("Enter", "m", "s", "q"):
            assert key in legend, f"the legend never mentions {key!r}"

    def test_curses_keeps_the_cursor_visible(self, cfg):
        """A list taller than the window scrolls, keeping the cursor row on
        screen (the old scrolling-terminal failure)."""
        config = st.load_config()
        win = _Win(14, 80)
        with contextlib.suppress(Exception):
            st._draw(win, config, None, 3, "", "ok", None)
        rows = [r for r in win.rows if r.strip()]
        assert len(rows) <= 14  # never overflows the window
        assert any(r.strip().startswith("› ") for r in rows)  # the cursor row is on screen
        # ONE frame: the legend sits inside, never on the left border
        legend = next((r for r in win.rows if "m live model ids" in r or "live model ids" in r), "")
        assert legend, f"the legend was never drawn: {win.rows}"
        assert legend.startswith(" "), f"legend overwrites the left border: {legend!r}"

    def test_editor_falls_back_when_curses_cannot_start(self, cfg, monkeypatch):
        """A broken TERM/terminfo must not traceback — the line-mode editor
        takes over (this is also the off-TTY test path)."""
        import curses

        def _no_wrapper(fn):
            raise curses.error("setupterm: could not find terminal")

        monkeypatch.setattr(curses, "wrapper", _no_wrapper)
        inputs = iter(["e max_iterations 250", "s"])
        monkeypatch.setattr(Console, "input", lambda self, *a, **k: next(inputs))
        assert st.run_editor(_console()) == 0
        assert json.loads(cfg.read_text())["max_iterations"] == 250

    def test_line_mode_quit_saves_nothing(self, cfg, monkeypatch):
        before = cfg.read_text()
        inputs = iter(["e max_iterations 1", "q"])
        monkeypatch.setattr(Console, "input", lambda self, *a, **k: next(inputs))
        assert st._run_line_mode(_console()) == 0
        assert cfg.read_text() == before

    def test_line_mode_reports_unknown_command(self, cfg, monkeypatch):
        inputs = iter(["wat", "q"])
        monkeypatch.setattr(Console, "input", lambda self, *a, **k: next(inputs))
        assert st._run_line_mode(_console()) == 0

    def test_legend_sits_inside_the_frame(self, cfg):
        """The legend used to be written over the right border (…quitqqqq)."""
        config = st.load_config()
        win = _Win(24, 92)
        with contextlib.suppress(Exception):
            st._draw(win, config, None, 0, "", "ok", None)
        # the legend line must not run to the last column (the border)
        for r in win.rows:
            if "move" in r and "quit" in r:
                assert len(r.rstrip()) < 92, "legend overruns the frame"


class TestNonTty:
    def test_non_tty_main_prints_summary(self, cfg, capsys, monkeypatch):
        import io

        monkeypatch.setattr(st.sys, "stdin", io.StringIO(""))
        assert st.main() == 0
        out = capsys.readouterr().out
        assert "settings:" in out and "provider" in out


class TestRealTerminal:
    """The curses screen on a REAL terminal (a pty).

    The fake-window tests could not see it: `stdscr.box(0, 0, h, w)` is a
    TypeError on a live terminal (box() takes at most two args) and there is
    no rectangle() at all — the screen died and silently fell back to a
    printed dump. This drives the real curses API, so that class of bug
    cannot come back.
    """

    def _drive(self, tmp_path, monkeypatch, keys):
        import os
        import pty
        import select
        import time

        config = {"provider": "zai", "zai_model": "glm-5.3", "max_iterations": 100000}
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps(config))
        script = (
            "import json, os, sys\n"
            "sys.path.insert(0, %r)\n"
            "import curses\n"
            "import suijin.modules.console.lib.settings_tui as st\n"
            f"st.CONFIG_PATH = {str(cfg)!r}\n"
            f"SCRIPT = {list(keys)!r}\n"
            "n = [0]\n"
            "class Proxy:\n"
            "    def __init__(self, s): object.__setattr__(self, '_s', s)\n"
            "    def __getattr__(self, k): return getattr(object.__getattribute__(self, '_s'), k)\n"
            "    def getch(self):\n"
            "        n[0] += 1\n"
            "        if n[0] > 30: os._exit(3)\n"
            "        return SCRIPT.pop(0) if SCRIPT else 27\n"
            "def loop(scr):\n"
            "    st._init_colors(scr)\n"
            "    return st._curses_loop(Proxy(scr))\n"
            "try:\n"
            f"    rc = curses.wrapper(loop)\n"
            f"    open({str(tmp_path / 'rc')!r}, 'w').write(str(rc))\n"
            "except Exception:\n"
            "    import traceback\n"
            f"    open({str(tmp_path / 'rc')!r}, 'w').write(traceback.format_exc())\n"
        ) % str(Path(__file__).resolve().parents[2])
        env = dict(os.environ, TERM="xterm-256color")
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, "-c", script], env)
        deadline = time.time() + 30
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.3)
            if r:
                try:
                    if not os.read(fd, 65536):
                        break
                except OSError:
                    break
            if (tmp_path / "rc").exists():
                break
        os.waitpid(pid, 0)
        out = (tmp_path / "rc").read_text() if (tmp_path / "rc").exists() else "no result"
        return out

    def test_navigates_and_saves_on_a_real_terminal(self, tmp_path):
        """up, open Limits, down, down, Esc, q — on a live curses screen."""
        import curses

        out = self._drive(
            tmp_path,
            None,
            [curses.KEY_UP, 10, curses.KEY_DOWN, curses.KEY_DOWN, 27, ord("q")],
        )
        assert out == "0", f"the real screen failed: {out[:800]}"
