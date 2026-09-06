"""
suijin/core/red/config_loader.py — Config & environment loading for Red Team.

Extracted from redteamer.py. Handles config.json creation/defaults,
Pydantic validation, and .env provider-key loading (interactive wizard
or non-interactive CI-safe mode).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from rich.console import Console

from suijin.modules.platform.lib.constants import (
    DEFAULT_MODEL,
    EXPERT_MODELS,
    GEMINI_MODEL,
    MAX_ITERATIONS,
    METASPLOIT_RPC_PORT,
    SENTINEL_MODEL,
    SUPERVISOR_MODEL,
    ZAI_ENDPOINT,
    ZAI_MODEL,
)

console = Console()
BASE_DIR = Path(__file__).resolve().parents[3]  # suijin/ package (was 3 parents pre-move)
ENV_PATH = BASE_DIR / ".env"


def active_model(config: dict | None) -> str:
    """Provider-aware model resolution for display and routing.

    `final_model_id` is a HuggingFace-style repo id and is ONLY meaningful
    for the huggingface provider — it must never leak as a cross-provider
    default (it used to: selecting zai displayed
    'zai / deepseek-ai/DeepSeek-V4-Flash' even while calling glm-5.3).
    """
    cfg = config or {}
    provider = cfg.get("provider", "deepseek")
    if provider == "huggingface":
        return cfg.get("final_model_id") or "auto"
    return cfg.get(f"{provider}_model") or "auto"


CONFIG_PATH = BASE_DIR / "config.json"


def _default_config() -> dict:
    return {
        "stealth": True,  # v5.1: quiet by default
        "provider": "deepseek",
        "expert_models": EXPERT_MODELS,
        "final_model_id": "deepseek-ai/DeepSeek-V4-Flash",
        "sentinel_model_id": SENTINEL_MODEL,
        "max_tokens_per_request": 8000,
        "temperature": 0.4,
        "metasploit_rpc_host": "127.0.0.1",
        "metasploit_rpc_port": METASPLOIT_RPC_PORT,
        "metasploit_rpc_ssl": False,
        "supervisor_model_id": SUPERVISOR_MODEL,
        "supervisor_interval": 5,
        "cost_alert_usd": 0.25,
        "cost_budget_usd": 1.0,
        "cost_hard_cap_usd": 2.0,
        "max_iterations": MAX_ITERATIONS,
    }


def load_config() -> dict:
    """Load config.json, creating defaults if missing. Validates with Pydantic.

    NEVER crashes on a bad path: a docker bind-mount turns config.json
    into a DIRECTORY when the host file is absent (compose creates the
    mount point), and `:ro` mounts refuse writes — both boot with
    defaults + ONE warning instead of a traceback wall."""
    if CONFIG_PATH.is_dir():
        console.print(
            "[bold red]config.json is a DIRECTORY — a docker bind-mount created it because "
            "suijin/config.json is missing on the host.[/bold red] Fix: rmdir suijin/config.json "
            "(or create the file), then restart. Using defaults for this run."
        )
    raw = None
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text())
            if not isinstance(raw, dict):
                raise ValueError(f"top level is {type(raw).__name__}, not an object")
        except Exception as e:  # noqa: BLE001 — a corrupt config never blocks boot
            console.print(f"[bold red]config.json unreadable ({type(e).__name__}: {e}) — using defaults.[/bold red]")
            raw = None
    config = dict(raw or {})
    if raw is None and not CONFIG_PATH.exists():
        # genuinely absent (not dir/corrupt) — seed the defaults file and
        # USE them (the cost caps below are the fresh-install posture);
        # read-only mounts just ride the in-memory defaults
        config = _default_config()
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=4)
        except OSError:
            pass
    for k, v in {
        "gemini_model": GEMINI_MODEL,
        "deepseek_model": DEFAULT_MODEL,
        "zai_model": ZAI_MODEL,
        "zai_endpoint": ZAI_ENDPOINT,
        "supervisor_model_id": SUPERVISOR_MODEL,
        "supervisor_interval": 5,
        "max_iterations": MAX_ITERATIONS,
    }.items():
        config.setdefault(k, v)
    # Validate with Pydantic — catch typos at startup
    try:
        from suijin.modules.platform.lib.config_models import RedConfig

        validated = RedConfig(**config)
        config.update(validated.model_dump())
    except Exception as e:
        import logging

        logging.getLogger("suijin").warning(f"Config validation failed: {e}. Using raw config.")
    return config


def _write_config(config: dict) -> bool:
    """Persist config.json; a read-only bind-mount (compose ':ro') refuses
    writes — ONE clear warning, never a crash. Returns success."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        return True
    except OSError as e:
        console.print(
            f"[bold red]cannot write config.json ({e.strerror or e}) — the mount is read-only. "
            "Set the provider by hand or fix the mount.[/bold red]"
        )
        return False


def add_custom_provider() -> bool:
    """Interactive: add an OpenAI-compatible provider — the user enters a
    base URL and an API key. The key is ANY string (no format policing by
    design — gateways, proxies and self-hosted boxes use arbitrary tokens;
    blank = keyless). Writes config.json custom_providers + switches
    provider to custom:<name>."""
    if not sys.stdin.isatty():
        console.print("[red]custom provider setup needs a terminal (interactive)[/red]")
        console.print(
            '[dim]hand-edit config.json: custom_providers=[{name, base_url, api_key, model}] + provider="custom:<name>"[/dim]'
        )
        return False
    console.print("[bold]Custom OpenAI-compatible provider[/bold]")
    console.print(
        "[dim]Any endpoint speaking /chat/completions — vLLM, LiteLLM, OpenRouter-style gateways, LAN boxes…[/dim]"
    )
    base = input("Base URL (e.g. https://host/v1 or http://10.0.0.5:8000/v1): ").strip()
    if not base:
        console.print("[red]base URL required[/red]")
        return False
    key = input("API key (anything — paste it; Enter = keyless): ").strip()
    model = input("Model id (e.g. llama4-maverick; Enter = decide later): ").strip()
    name = input("Name for this provider [custom]: ").strip() or "custom"
    config = load_config()
    entries = [e for e in (config.get("custom_providers") or []) if str(e.get("name", "")).strip() != name]
    entry = {"name": name, "base_url": base, "api_key": key}
    if model:
        entry["model"] = model
    entries.append(entry)
    config["custom_providers"] = entries
    config["provider"] = f"custom:{name}"
    if not _write_config(config):
        return False
    console.print(f"[green]provider set to custom:{name} → {base}[/green]")
    console.print("[dim]switch anytime: suijin config · test: suijin providers[/dim]")
    return True


def load_env():
    """Load API keys from .env. Interactive wizard on TTY, no-op on CI."""
    if not ENV_PATH.exists():
        # Non-interactive mode (CI, pytest, piped stdin) — skip setup wizard
        if not sys.stdin.isatty():
            return
        console.print("[bold yellow][!] .env file missing.[/bold yellow]")
        console.print("[bold white]Select AI Provider:[/bold white]")
        console.print("  [bold #ff5555]1.[/] [white]Hugging Face[/]")
        console.print("  [bold #5555ff]2.[/] [white]AMD Cloud[/]")
        console.print("  [bold #e6b47c]3.[/] [white]Gemini[/]")
        console.print("  [bold #58a6ff]4.[/] [white]DeepSeek[/]")
        console.print("  [bold #c586c0]5.[/] [white]Z.ai (GLM)[/]")
        console.print("  [bold #e6b47c]6.[/] [white]OpenRouter (one key → every major model)[/]")
        console.print("  [bold #58a6ff]7.[/] [white]OpenAI[/]")
        console.print("  [dim white]  …every registry provider activates when its KEY appears in .env[/]")
        console.print("  [bold green]8.[/] [white]Local (Ollama — no key, free)[/]")
        console.print("  [bold magenta]9.[/] [white]Custom OpenAI-compatible (your base URL + API key)[/]")
        choice = input("Choice [1-9]: ").strip()
        config = load_config()

        def _save(provider_key: str, env_name: str, key: str):
            config["provider"] = provider_key
            _write_config(config)
            try:
                ENV_PATH.write_text(f"{env_name}={key}\n" if env_name else "")
            except OSError as e:
                console.print(
                    f"[bold red]cannot write .env ({e.strerror or e}) — export {env_name} by hand.[/bold red]"
                )
                return
            if env_name:
                os.environ[env_name] = key

        _registry_pick = {
            "6": ("openrouter", "OPENROUTER_API_KEY"),
            "7": ("openai", "OPENAI_API_KEY"),
        }
        if choice in _registry_pick:
            pk, env_name = _registry_pick[choice]
            key = input(f"Enter {env_name}: ").strip()
            _save(pk, env_name, key)
        elif choice == "8":
            _save("ollama", "", "")
            console.print("[green]Local mode: ollama serve + ollama pull <model>[/green]")
        elif choice == "9":
            if add_custom_provider() and not ENV_PATH.exists():
                ENV_PATH.write_text("")  # no env key needed — stop the wizard nagging
        elif choice == "2":
            key = input("Enter AMD_API_KEY: ").strip()
            _save("amd", "AMD_API_KEY", key)
        elif choice == "3":
            key = input("Enter GEMINI_API_KEY: ").strip()
            _save("gemini", "GEMINI_API_KEY", key)
        elif choice == "4":
            key = input("Enter DEEPSEEK_API_KEY: ").strip()
            _save("deepseek", "DEEPSEEK_API_KEY", key)
        elif choice == "5":
            key = input("Enter ZAI_API_KEY: ").strip()
            _save("zai", "ZAI_API_KEY", key)
        else:
            token = input("Enter HF_TOKEN: ").strip()
            _save("huggingface", "HF_TOKEN", token)
    else:
        if ENV_PATH.is_file():  # a bind-mount could make this a directory too
            for line in ENV_PATH.read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()
