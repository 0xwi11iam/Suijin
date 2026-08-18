"""Shared runtime state for Suijin tools.

Holds the module-level state that the split tool modules share: the HTTP
session, proxy, recon counter, knowledge-base path, and a handful of small
helpers.

Phase 0, item 2: importing this module is now SIDE-EFFECT-FREE. The one-time
work it used to do at import (module-pack discovery, TLS warning
suppression, workspace migration + mkdirs) moved behind init_runtime(),
which the entry points (cli/main/mcp/ui) call explicitly exactly once.
A lazy auto-init guard keeps any unmigrated consumer working: the first
touch of the session/state accessors initializes on demand, so nothing
breaks mid-migration — but the explicit call remains the contract.
"""

from __future__ import annotations

import threading
from pathlib import Path

import requests
import urllib3

from suijin.kb import DB_PATH  # noqa: F401 — deliberate re-export; kb.py owns the path
from suijin.tools.workspace import WORKSPACE_DIR, ensure_workspace_layout

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent

# ── One-time initialization (explicit; entry points call this) ────────

_initialized = False
_init_lock = threading.Lock()


def init_runtime(force: bool = False) -> None:
    """One-time process initialization. Idempotent and thread-safe.

    Order matters: workspace layout BEFORE the mkdirs that live in it.
    """
    global _initialized
    if _initialized and not force:
        return
    with _init_lock:
        if _initialized and not force:
            return
        from suijin.modules.loader import discover_modules

        discover_modules()
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # Workspace layout: merge any legacy suijin/suijin_agent real dir
        # into the canonical root workspace and symlink the inner path.
        ensure_workspace_layout()
        for sub in ("payloads", "scripts", "outputs"):
            (WORKSPACE_DIR / sub).mkdir(parents=True, exist_ok=True)
        _initialized = True


def _ensure_initialized() -> None:
    """Lazy guard for consumers that predate the explicit-init contract."""
    if not _initialized:
        init_runtime()


def is_initialized() -> bool:
    return _initialized


# ── Shared state (imported by tool modules; access auto-initializes) ──

_recon_state = {"exploration_count": 0}


def reset_recon_state():
    """Reset the exploration counter (call at start of new engagement)."""
    _recon_state["exploration_count"] = 0


_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()

_session: requests.Session | None = None
_session_lock = threading.Lock()
_proxy_url = None


def _get_session() -> requests.Session:
    _ensure_initialized()
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = requests.Session()
    return _session


class _SessionProxy:
    """Module-level `global_session` compatibility shim.

    Attribute access forwards to the real (lazily created) Session so
    legacy `from .runtime import global_session` keeps working while
    guaranteeing the runtime is initialized before any HTTP happens.
    """

    def __getattr__(self, name):
        return getattr(_get_session(), name)


global_session = _SessionProxy()


def set_proxy(url: str | None):
    """Set a global proxy for all HTTP requests. Call at startup from config."""
    global _proxy_url
    _proxy_url = url
    sess = _get_session()
    if url:
        sess.proxies = {"http": url, "https": url}
    else:
        sess.proxies = {}


def get_proxy() -> str | None:
    return _proxy_url


def truncate(text, limit=50000):
    if len(text) > limit:
        return text[:limit] + f"\n\n[TRUNCATED at {limit} chars — {len(text)} total]"
    return text
