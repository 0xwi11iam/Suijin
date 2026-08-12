"""
medusa/core/paths.py — Centralized, configurable path constants.

All /tmp paths are derived from MEDUSA_TMP_DIR env var.
Set MEDUSA_TMP_DIR to customize (default: /tmp).
"""
import os
from pathlib import Path

TMP = Path(os.environ.get("MEDUSA_TMP_DIR", "/tmp"))


def tmp_path(name: str) -> Path:
    """Get a path under the configured temp directory."""
    p = TMP / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# Blue team paths
BLUE_KG_PATH = tmp_path("blue_kg.json")
BLUE_TRAFFIC_LOG = tmp_path("blue_defend_traffic.jsonl")
BLUE_TARPIT_FILE = tmp_path("blue_tarpit.json")
BLUE_HONEYPOTS_FILE = tmp_path("blue_honeypots.json")
BLUE_DB = tmp_path("blue_defend.db")
BLUE_UPLOADS = tmp_path("blue_uploads")
BLUE_PROXY_LOG = tmp_path("blue_proxy_{port}.jsonl")
BLUE_CANARY_LOG = tmp_path("canary_triggers.json")

# Red team paths
MEDUSA_LOG_DIR = tmp_path("medusa_logs")
