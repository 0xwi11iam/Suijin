"""
suijin/core/blue/config.py — Blue team operational configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
# v4.1: operator tuning state — lives in the workspace (the volume),
# auto-created from DEFAULT_BLUE_CONFIG on first load. Lazy accessor
# (boundary rule) honouring a monkeypatched module attr.
CONFIG_PATH = None


def _config_path():
    v = globals().get("CONFIG_PATH")
    if v is not None:
        return v  # monkeypatched / set by the operator
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "blue_config.json"


def __getattr__(name):
    if name == "CONFIG_PATH":
        return _config_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


DEFAULT_BLUE_CONFIG = {
    "traffic_normalization_turns": 10,
    "scorer": {
        "sql_keywords_weight": 4,
        "xss_pattern_weight": 3,
        "path_traversal_weight": 3,
        "unknown_param_weight": 2,
        "new_ip_weight": 2,
        "unusual_method_weight": 2,
        "body_size_anomaly_weight": 1,
        "burst_penalty": 1,
        "critical_threshold": 8,
        "suspicious_threshold": 5,
    },
    "watchers": {
        "max_per_endpoint": 3,
        "spawn_on_threshold": 20,  # requests/min before spawning extra watcher
        "health_check_interval": 30,
        "context_rotation_requests": 200,
    },
    "deception": {
        "auto_honeypot": True,
        "auto_tarpit": True,
        "tarpit_delay_seconds": 8,
        "max_tarpit_requests": 100,
        "canary_tokens": True,
        "shadow_redirect_threshold": 8,  # score 8+ → shadow redirect
    },
    "response": {
        "auto_block_critical": True,
        "auto_block_suspicious": False,  # operator approval needed
        "max_blocks_per_hour": 50,
        "unblock_after_hours": 24,
    },
    "hotfix": {
        "auto_patch_critical": False,  # operator approval always
        "patch_timeout_minutes": 5,
        "test_before_deploy": True,
        "silent_patch_mode": True,  # patch but keep vulnerable endpoint as trap
    },
    "soc": {
        "tier1_per_endpoint": True,
        "tier2_count": 3,
        "threat_hunter_count": 1,
        "shift_check_interval": 60,
    },
    "cost": {
        "daily_budget_usd": 5.00,
        "alert_threshold_usd": 3.00,
        "max_llm_calls_per_minute": 20,
    },
}


def load_blue_config() -> dict:
    if _config_path().exists():
        loaded = json.loads(_config_path().read_text())
        merged = dict(DEFAULT_BLUE_CONFIG)
        _deep_merge(merged, loaded)
    else:
        merged = dict(DEFAULT_BLUE_CONFIG)
        _config_path().write_text(json.dumps(DEFAULT_BLUE_CONFIG, indent=2))

    # Validate with Pydantic model — catches typos and bad values at startup
    try:
        from suijin.modules.platform.lib.config_models import BlueConfig

        validated = BlueConfig(**merged)
        return validated.model_dump()
    except Exception as e:
        import logging

        logging.getLogger("suijin").warning(f"Blue config validation failed: {e}. Using raw config.")
        return merged


def save_blue_config(config: dict) -> None:
    _config_path().write_text(json.dumps(config, indent=2))


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
