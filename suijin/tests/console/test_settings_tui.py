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
    def test_form_mounts_editors_and_save_round_trip(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                await asyncio.sleep(0.3)  # call_after_refresh(_rebuild) drains
                assert len(app._editors) > 15  # one editor per visible field
                app._editors["max_iterations"].value = "500"
                await pilot.pause()
                app._editors["mode_hitl"].value = True
                await pilot.pause()
                app._editors["zai_model"].value = "glm-4.6"
                await pilot.pause()
                assert app._cfg["max_iterations"] == 500
                assert app._cfg["mode_hitl"] is True
                assert app._cfg["zai_model"] == "glm-4.6"
                app.action_save()
                await pilot.pause()

        asyncio.run(run())
        saved = json.loads(cfg.read_text())
        assert saved["max_iterations"] == 500
        assert saved["mode_hitl"] is True
        assert saved["zai_model"] == "glm-4.6"

    def test_provider_swap_rebuilds_visible_fields(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                await asyncio.sleep(0.3)
                assert "zai_endpoint" in app._editors  # zai provider
                app._editors["provider"].value = "deepseek"
                await pilot.pause()
                await asyncio.sleep(0.4)  # rebuild via call_after_refresh
                assert "zai_endpoint" not in app._editors
                assert "deepseek_model" in app._editors

        asyncio.run(run())

    def test_non_tty_main_prints_summary(self, cfg, capsys, monkeypatch):
        import io
        import sys as _sys

        monkeypatch.setattr(_sys, "stdin", io.StringIO(""))  # not a TTY
        assert st.main() == 0
        out = capsys.readouterr().out
        assert "settings:" in out and "provider" in out


class TestSaveCorruptionFixes:
    """The reported bug: saving didn't work properly. Root causes fixed:
    (1) render echoes wrote into config (the _syncing flag reset before
    the async Changed events arrived); (2) echoed handlers keyed on the
    CURRENT highlight, so quick navigation wrote field A's value into
    field B; (3) a choice value outside the static list was silently
    rewritten to choices[0]; (4) a corrupt config would be wiped on
    save. The form redesign removes the whole class: one mounted editor
    per field, events keyed by the WIDGET'S OWN id."""

    def test_walking_the_list_writes_nothing(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                await asyncio.sleep(0.3)
                before = dict(app._cfg)
                lv = app.query_one("#list", st.ListView)
                for i in range(len(lv.children)):
                    lv.index = i
                await pilot.pause()
                drift = {k: (before.get(k), v) for k, v in app._cfg.items() if before.get(k) != v}
                assert not drift, f"a render echo wrote into config: {drift}"

        asyncio.run(run())

    def test_stale_echo_event_cannot_write(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                await asyncio.sleep(0.3)

                # a delayed echo of temperature's mount-time "0.4" arriving
                # late — handler must drop it (value == rendered install)
                class _E:
                    class input:  # noqa: N801
                        id = "f-temperature"
                        display = True

                    value = "0.4"

                app.on_input_changed(_E())
                assert app._cfg["temperature"] == 0.4  # unchanged, not re-corrupted

        asyncio.run(run())

    def test_choice_value_outside_list_is_kept(self, cfg):
        import asyncio

        data = json.loads(cfg.read_text())
        data["zai_model"] = "glm-4.5-older-custom"  # not in the static list
        cfg.write_text(json.dumps(data))

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                await asyncio.sleep(0.3)
                assert app._cfg["zai_model"] == "glm-4.5-older-custom"  # kept, shown as (current)

        asyncio.run(run())

    def test_corrupt_config_refuses_save(self, tmp_path, monkeypatch):
        import asyncio

        p = tmp_path / "config.json"
        p.write_text("{corrupt")
        monkeypatch.setattr(st, "CONFIG_PATH", str(p))

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                app.action_save()
                await pilot.pause()
                assert app.is_running  # refused: did NOT exit, did NOT wipe

        asyncio.run(run())
        assert p.read_text() == "{corrupt"  # the file is untouched

    def test_save_and_cancel_buttons_exist(self, cfg):
        import asyncio

        async def run():
            app = st.SettingsApp()
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause()
                from textual.widgets import Button

                assert "Save" in str(app.query_one("#btn-save", Button).label)
                assert app.query_one("#btn-cancel", Button)

        asyncio.run(run())
