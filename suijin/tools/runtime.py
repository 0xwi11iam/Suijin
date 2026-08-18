"""Shared runtime state for Suijin tools.

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

from suijin.kb import DB_PATH  # noqa: F401 — deliberate re-export; kb.py owns the path
from suijin.modules.loader import discover_modules
from suijin.tools.workspace import WORKSPACE_DIR, ensure_workspace_layout

# ── Module packs: discover once at import (idempotent) ────────────────
discover_modules()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent

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


# Workspace layout: merge any legacy suijin/suijin_agent real dir into the
# canonical root workspace and replace it with a symlink (idempotent).
ensure_workspace_layout()

# Ensure workspace subdirectories exist
(WORKSPACE_DIR / "payloads").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "scripts").mkdir(parents=True, exist_ok=True)
(WORKSPACE_DIR / "outputs").mkdir(parents=True, exist_ok=True)


def truncate(text, limit=50000):
    if len(text) > limit:
        return text[:limit] + f"\n\n[TRUNCATED at {limit} chars — {len(text)} total]"
    return text
