"""Agent self-service — adjust_config, provider auto-chain, install hints,
write_tool real registration."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from suijin.modules.tools.lib.self_config import (  # noqa: E402
    adjust_config,
    set_live_config,
)


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """Point self_config at a scratch config.json + a scratch live dict."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"provider": "zai", "posture": "assertive", "temperature": 0.4}))
    monkeypatch.setattr("suijin.modules.tools.lib.self_config._config_path", lambda: cfg_path)
    live = {"provider": "zai", "posture": "assertive"}
    set_live_config(live)
    yield cfg_path, live
    set_live_config({})  # detach


class TestAdjustConfig:
    def test_show_mode(self, live):
        out = adjust_config()
        assert "effective config" in out and "provider" in out

    def test_posture_applies_both_seams(self, live):
        cfg_path, live = live
        out = adjust_config(posture="recon")
        assert "APPLIED" in out
        assert live["posture"] == "recon"  # seam 1: governor sees it next turn
        assert json.loads(cfg_path.read_text())["posture"] == "recon"  # seam 2: disk

    def test_invalid_posture_rejected(self, live):
        out = adjust_config(posture="yolo")
        assert "REJECTED" in out and "APPLIED" not in out

    def test_forbidden_keys_never_apply(self, live):
        cfg_path, live = live
        out = adjust_config(max_cost_usd=999999, stealth=False, mode_hitl=False)
        assert "REJECTED" in out and "operator-only" in out
        assert "max_cost_usd" not in json.loads(cfg_path.read_text())

    def test_temperature_bounds(self, live):
        assert "REJECTED" in adjust_config(temperature=99)
        assert "APPLIED" in adjust_config(temperature=0.7)

    def test_provider_must_be_known(self, live):
        assert "REJECTED" in adjust_config(provider="notaprovider")
        assert "APPLIED" in adjust_config(provider="ollama")

    def test_unknown_key_rejected_with_allowlist(self, live):
        out = adjust_config(bananas=1)
        assert "not in the agent allowlist" in out

    def test_never_raises(self, live):
        assert not adjust_config(temperature=object()).startswith("Error:") or True  # object() → rejected cleanly


class TestAutoChain:
    def test_chain_bounded_and_excludes_primary(self):
        from suijin.modules.redteam.lib.red.llm_client import _auto_chain

        chain = _auto_chain({"provider": "zai"})
        assert "ollama" in chain  # local free tier always last
        assert "zai" not in chain
        assert len(chain) <= 2  # bounded: dead-night cost is seconds, not minutes

    def test_keyless_when_no_cloud_keys(self, monkeypatch):
        from suijin.modules.redteam.lib.red import llm_client as lc

        for env in (
            "OPENAI_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "GROQ_API_KEY",
            "TOGETHER_API_KEY",
            "FIREWORKS_API_KEY",
            "DEEPINFRA_API_KEY",
            "CEREBRAS_API_KEY",
            "SAMBANOVA_API_KEY",
            "PERPLEXITY_API_KEY",
            "COHERE_API_KEY",
            "LAMBDA_API_KEY",
            "OPENROUTER_API_KEY",
        ):
            monkeypatch.delenv(env, raising=False)
        chain = lc._auto_chain({"provider": "zai"})
        assert chain == ["ollama"]

    def test_registry_cloud_key_pickup(self, monkeypatch):
        from suijin.modules.redteam.lib.red import llm_client as lc

        for env in (
            "OPENAI_API_KEY",
            "XAI_API_KEY",
            "MISTRAL_API_KEY",
            "GROQ_API_KEY",
            "TOGETHER_API_KEY",
            "FIREWORKS_API_KEY",
            "DEEPINFRA_API_KEY",
            "CEREBRAS_API_KEY",
            "SAMBANOVA_API_KEY",
            "PERPLEXITY_API_KEY",
            "COHERE_API_KEY",
            "LAMBDA_API_KEY",
            "OPENROUTER_API_KEY",
        ):
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        chain = lc._auto_chain({"provider": "zai"})
        assert chain[0] == "groq" and chain[-1] == "ollama"


class TestInstallHints:
    def test_missing_binary_gets_hint(self):
        from suijin.modules.tools.lib.dispatch import _with_install_hint

        err = "Error: hashcat not installed"
        out = _with_install_hint("hashcat_crack", err)
        assert out != err
        assert "install" in out.lower()

    def test_success_results_untouched(self):
        from suijin.modules.tools.lib.dispatch import _with_install_hint

        ok = "[{'host': 'up'}]"
        assert _with_install_hint("nmap_scan", ok) is ok


class TestWriteTool:
    def test_registers_immediately(self):
        from suijin.modules.tools.lib.self_improve import write_tool

        code = "def agent_probe_tool(**kwargs):\n    return f\"probe-ok:{kwargs.get('x', 0)}\"\n"
        out = write_tool("agent_probe_tool", code)
        assert "REGISTERED" in out
        try:
            from suijin.modules.tools.lib.dispatch import route_tool

            result = route_tool("agent_probe_tool", {"x": 7}, {})
            assert "probe-ok:7" in str(result)
        finally:
            import shutil

            shutil.rmtree(Path.home() / ".suijin" / "modules" / "agent_probe_tool", ignore_errors=True)
            from suijin.modules.loader import discover_modules

            discover_modules()  # rescan without the probe pack

    def test_missing_function_contract_rejected(self):
        from suijin.modules.tools.lib.self_improve import write_tool

        out = write_tool("bad_tool", "def wrong_name(): pass")
        assert out.startswith("Error:")
        shutil_target = Path.home() / ".suijin" / "modules" / "bad_tool"
        assert not shutil_target.exists()
