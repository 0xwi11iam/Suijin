"""Shared runtime state for Medusa tools.

Holds the module-level state that the split tool modules share: the HTTP
session, proxy, recon counter, knowledge-base path, and a handful of small
helpers. Importing this module performs one-time initialization
(module discovery, TLS warning suppression, workspace directories).
"""
from __future__ import annotations

import threading
from pathlib import Path

import requests
import urllib3

from medusa.modules.loader import discover_modules
from medusa.tools.workspace import WORKSPACE_DIR

# ── Module packs: discover once at import (idempotent) ────────────────
discover_modules()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent
DB_PATH = BASE_DIR / "kb.sqlite3"

MCP_SERVERS: dict[str, list] = {}


def get_server_for_tool(tool_name: str) -> list:
    return MCP_SERVERS.get(tool_name, [])


AI_SERVICE_ENDPOINTS: dict = {}


def fingerprint_ai_response(response_json: dict) -> str:
    return "unknown"


_recon_state = {"exploration_count": 0}


def reset_recon_state():
    """Reset the exploration counter (call at start of new engagement)."""
    _recon_state["exploration_count"] = 0


_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()

global_session = requests.Session()
_proxy_url = None


def set_proxy(url: str | None):
    """Set a global proxy for all HTTP requests. Call at startup from config."""
    global _proxy_url
    _proxy_url = url
    if url:
        global_session.proxies = {"http": url, "https": url}
    else:
        global_session.proxies = {}


def get_proxy() -> str | None:
    return _proxy_url


# Ensure workspace subdirectories exist
(WORKSPACE_DIR / "payloads").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "scripts").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "outputs").mkdir(parents=True, exist_ok=True)


def truncate(text, limit=50000):
    if len(text) > limit:
        return text[:limit] + f"\n\n[TRUNCATED at {limit} chars — {len(text)} total]"
    return text
