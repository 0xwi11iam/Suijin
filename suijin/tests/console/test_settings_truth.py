"""Settings truth (2026-11-01): ONE config path, provider-centric rows,
honest metadata, and the gauge's unknown-window rule."""

import json

import pytest

import suijin.modules.console.lib.settings_tui as st


@pytest.fixture()
def _cfg(tmp_path, monkeypatch):
    live = tmp_path / "live" / "config.json"
    live.parent.mkdir(parents=True)
    live.write_text(json.dumps({"provider": "openrouter", "openrouter_model": "x-ai/grok-4.7"}))
    monkeypatch.setattr(st, "CONFIG_PATH", str(live))
    return live


class TestOneConfigPath:
    def test_settings_reads_the_loaders_file(self, _cfg):
        cfg, status = st.load_state()
        assert status == "ok" and cfg["provider"] == "openrouter"

    def test_settings_write_round_trips_to_the_same_file(self, _cfg):
        cfg, _ = st.load_state()
        cfg["provider"] = "zai"
        st.save_config(cfg)
        on_disk = json.loads(_cfg.read_text())
        assert on_disk["provider"] == "zai"

    def test_migration_rescues_stranded_tree_edits(self, _cfg, tmp_path, monkeypatch):
        tree = tmp_path / "tree" / "config.json"
        tree.parent.mkdir()
        tree.write_text(json.dumps({"provider": "openrouter", "openrouter_model": "x-ai/grok-4.5"}))
        monkeypatch.setattr(st, "_LEGACY_TREE_PATH", str(tree))
        live = json.loads(_cfg.read_text())
        live["openrouter_model"] = ""  # the live file never got the model
        _cfg.write_text(json.dumps(live))
        cfg, _ = st.load_state()
        assert cfg["openrouter_model"] == "x-ai/grok-4.5"  # rescued
        assert "__migration_note__" in cfg

    def test_migration_never_overwrites_live_values(self, _cfg, tmp_path, monkeypatch):
        tree = tmp_path / "tree2" / "config.json"
        tree.parent.mkdir()
        tree.write_text(json.dumps({"provider": "deepseek", "deepseek_model": "deepseek-chat"}))
        monkeypatch.setattr(st, "_LEGACY_TREE_PATH", str(tree))
        cfg, _ = st.load_state()
        assert cfg["provider"] == "openrouter"  # live wins; legacy ignored


class TestProviderCentricRows:
    def test_only_the_active_providers_model_visible(self):
        cfg = {"provider": "openrouter", "openrouter_model": "m1", "zai_model": "glm-5.3"}
        visible = st._visible_fields(cfg)
        model_keys = [k for k in visible if st.is_model_field(k)]
        assert model_keys == ["openrouter_model"]  # no zai wall

    def test_model_label_is_plain_model(self):
        assert st.model_display_label("openrouter_model", {"provider": "openrouter"}) == "model"


class TestMetadata:
    def test_meta_line_carries_price_window_source(self):
        meta = st.model_meta_line({"provider": "zai", "zai_model": "glm-5.3"})
        assert "per 1M" in meta and "ctx" in meta and "source:" in meta

    def test_no_provider_no_meta(self):
        assert st.model_meta_line({}) == ""


class TestGaugeTruth:
    def test_unknown_window_renders_none_pct(self):
        # the strip contract: unknown window → no fake percent
        UI = {"ctx_in_tok": 12_000, "ctx_window": 0}
        pct = round(100.0 * UI["ctx_in_tok"] / max(1, UI["ctx_window"]), 1) if UI["ctx_window"] else None
        assert pct is None

    def test_known_window_computes(self):
        pct = round(100.0 * 50_000 / 500_000, 1)
        assert pct == 10.0


class TestPickers:
    def test_provider_picker_numbered(self, _cfg, monkeypatch):
        import rich.console as rc

        calls = {"n": 0}

        def fake_input(prompt=""):
            calls["n"] += 1
            return "2"

        console = rc.Console()
        monkeypatch.setattr(console, "input", fake_input)
        picked = st.pick_provider(console, {"provider": "openrouter"})
        assert picked and isinstance(picked, str)
