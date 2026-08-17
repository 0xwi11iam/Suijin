"""
medusa/core/red/config_loader.py — Config & environment loading for Red Team.

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

from medusa.core.constants import (
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
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # medusa/ directory
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.json"


def load_config() -> dict:
    """Load config.json, creating defaults if missing. Validates with Pydantic."""
    if not CONFIG_PATH.exists():
        default_config = {
            "provider": "deepseek",
            "expert_models": EXPERT_MODELS,
            "final_model_id": "deepseek-ai/DeepSeek-V4-Flash",
            "sentinel_model_id": SENTINEL_MODEL,
            "max_tokens_per_request": 8000, "temperature": 0.4,
            "use_database_framework": False, "use_local_bin_folder": False,
            "agent_workspace": "medusa_agent",
            "metasploit_rpc_host": "127.0.0.1", "metasploit_rpc_port": METASPLOIT_RPC_PORT,
            "metasploit_rpc_ssl": False,
            "supervisor_model_id": SUPERVISOR_MODEL,
            "supervisor_interval": 5, "cost_alert_usd": 0.25,
            "cost_budget_usd": 1.0, "cost_hard_cap_usd": 2.0,
            "max_iterations": MAX_ITERATIONS,
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(default_config, f, indent=4)
    config = json.loads(CONFIG_PATH.read_text())
    for k, v in {"gemini_model": GEMINI_MODEL, "deepseek_model": DEFAULT_MODEL,
                  "zai_model": ZAI_MODEL, "zai_endpoint": ZAI_ENDPOINT,
                  "supervisor_model_id": SUPERVISOR_MODEL,
                  "supervisor_interval": 5, "max_iterations": MAX_ITERATIONS}.items():
        config.setdefault(k, v)
    # Validate with Pydantic — catch typos at startup
    try:
        from medusa.core.config_models import RedConfig
        validated = RedConfig(**config)
        config.update(validated.model_dump())
    except Exception as e:
        import logging
        logging.getLogger("medusa").warning(f"Config validation failed: {e}. Using raw config.")
    return config


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
        choice = input("Choice [1-5]: ").strip()
        config = load_config()
        if choice == "2":
            config["provider"] = "amd"
            with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=4)
            key = input("Enter AMD_API_KEY: ").strip()
            ENV_PATH.write_text(f"AMD_API_KEY={key}\n")
            os.environ["AMD_API_KEY"] = key
        elif choice == "3":
            config["provider"] = "gemini"
            with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=4)
            key = input("Enter GEMINI_API_KEY: ").strip()
            ENV_PATH.write_text(f"GEMINI_API_KEY={key}\n")
            os.environ["GEMINI_API_KEY"] = key
        elif choice == "4":
            config["provider"] = "deepseek"
            with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=4)
            key = input("Enter DEEPSEEK_API_KEY: ").strip()
            ENV_PATH.write_text(f"DEEPSEEK_API_KEY={key}\n")
            os.environ["DEEPSEEK_API_KEY"] = key
        elif choice == "5":
            config["provider"] = "zai"
            with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=4)
            key = input("Enter ZAI_API_KEY: ").strip()
            ENV_PATH.write_text(f"ZAI_API_KEY={key}\n")
            os.environ["ZAI_API_KEY"] = key
        else:
            token = input("Enter HF_TOKEN: ").strip()
            ENV_PATH.write_text(f"HF_TOKEN={token}\n")
            os.environ["HF_TOKEN"] = token
    else:
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()
