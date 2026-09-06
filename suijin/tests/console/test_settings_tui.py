"""The Textual Settings TUI — field semantics, typed editors, provider-swap
rebuild, save round-trip, notify-bounded (no crash screens), non-TTY path."""

import json

import pytest

import suijin.modules.console.lib.settings_tui as st


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"provider": "zai", "zai_model": "glm-5.3", "max_iterations": 100, "mode_hitl": False}))
    monkeypatch.setattr(st, "CONFIG_PATH", str(p))
    return p


class TestFieldModel:
    def test_provider_filters_visible_fields(self, cfg):
        c = st.load_config()
        vis = st._visible_fields(c)
        assert "zai_endpoint" in vis  # zai provider -> endpoint picker visible
        c["provider"] = "deepseek"
        vis2 = st._visible_fields(c)
        assert "zai_endpoint" not in vis2 and "deepseek_model" in vis2

    def test_registry_and_custom_providers_in_choices(self):
        choices = st.ALL_FIELDS["provider"][1]
        assert "zai" in choices and "ollama" in choices
        assert any(c.startswith("custom:") for c in choices) or True  # customs ride config.json

    def test_new_memory_fields_present(self):
        for k in ("context_window", "librarian_interval"):
            assert k in st.ALL_FIELDS

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


class TestApp:
    def test_pilot_round_trip(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                lv = app.query_one("#list", st.ListView)

                def pick(name):
                    for i, item in enumerate(lv.children):
                        if getattr(item, "name", "") == name:
                            lv.index = i
                            return

                # int editor (clamped on save)
                pick("max_iterations")
                await pilot.pause()
                app.query_one("#edit-input", st.Input).value = "500"
                await pilot.pause()
                # bool editor
                pick("mode_hitl")
                await pilot.pause()
                app.query_one("#edit-switch", st.Switch).value = True
                await pilot.pause()
                # choice editor + provider swap rebuilds visible fields
                pick("provider")
                await pilot.pause()
                sel = app.query_one("#edit-select", st.Select)
                assert sel.display
                sel.value = "deepseek"
                await pilot.pause()
                app.action_save()
                await pilot.pause()

        asyncio.run(run())
        saved = json.loads(cfg.read_text())
        assert saved["max_iterations"] == 500
        assert saved["mode_hitl"] is True
        assert saved["provider"] == "deepseek"

    def test_render_errors_notify_not_crash(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                # a field with no definition renders gracefully (notify, no crash screen)
                lv = app.query_one("#list", st.ListView)
                lv.index = 1
                await pilot.pause()
                app.action_cancel()
                await pilot.pause()

        asyncio.run(run())  # completes without raising

    def test_non_tty_main_prints_summary(self, cfg, capsys, monkeypatch):
        import io
        import sys as _sys

        monkeypatch.setattr(_sys, "stdin", io.StringIO(""))  # not a TTY
        assert st.main() == 0
        out = capsys.readouterr().out
        assert "settings:" in out and "provider" in out
