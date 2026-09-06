"""Model metadata — the context window resolver.

The agent had ZERO knowledge of context-window sizes: budgets were
hardcoded (24k-char embed, 120k-char compaction trigger) regardless of
whether the model had 8k or 1M tokens. Resolution order:

  1. explicit override: config["context_window"] (operator-only,
     not agent-mutable) — 0/absent = auto
  2. the models.dev catalog (one JSON, every provider/model), fetched
     online, cached in the workspace with a 7-day TTL
  3. ANY failure (offline, timeout, unknown model) -> assume 1M tokens
     (the operator's fallback: never block an engagement on metadata)

Never raises, never blocks a call; a metadata lookup that costs the run
anything is a bug.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time

# the fallback when nothing resolves: assume 1M tokens
DEFAULT_CONTEXT_WINDOW = 1_000_000

# chars-per-token estimate for budget math (the codebase's ÷4 convention)
CHARS_PER_TOKEN = 4

_MODELS_DEV_URL = "https://models.dev/api.json"
_TTL_S = 7 * 24 * 3600  # cache the catalog for a week
_FAIL_BACKOFF_S = 3600  # a failed fetch retries at most hourly (offline hosts)

_lock = threading.Lock()
_last_fetch_attempt: float = 0.0
_negative_cache = False  # True after a failed fetch inside the backoff window


def _cache_path():
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR  # function-local (boundary law)

    return WORKSPACE_DIR / "caches" / "models.dev.json"


def _load_cached() -> dict | None:
    with contextlib.suppress(Exception):
        p = _cache_path()
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(data.get("_fetched_at", 0)) > _TTL_S:
            return None  # stale — refetch
        return data
    return None


def _fetch_catalog() -> dict | None:
    """Online fetch of models.dev; None on any failure (never raises)."""
    global _last_fetch_attempt
    with _lock:
        _last_fetch_attempt = time.time()
    try:
        import requests

        r = requests.get(_MODELS_DEV_URL, timeout=5)
        r.raise_for_status()
        catalog = r.json()
        if not isinstance(catalog, dict):
            raise ValueError("catalog is not an object")
        payload = {"_fetched_at": time.time(), "providers": catalog}
        with contextlib.suppress(Exception):
            _cache_path().parent.mkdir(parents=True, exist_ok=True)
            _cache_path().write_text(json.dumps(payload), encoding="utf-8")
        with _lock:
            _negative_cache = False
        return payload
    except Exception:  # noqa: BLE001 — metadata must never break a run
        with _lock:
            _negative_cache = True
        return None


def _catalog() -> dict | None:
    global _negative_cache
    cached = _load_cached()
    if cached is not None:
        return cached
    # a recent failed fetch backs off (offline hosts don't get hammered)
    with _lock:
        if _negative_cache and time.time() - _last_fetch_attempt < _FAIL_BACKOFF_S:
            return None
    return _fetch_catalog()


def _lookup_in_catalog(catalog: dict, provider: str, model: str) -> int:
    """Provider id → models dict → exact model, then substring match.
    Returns the window in TOKENS, 0 when not found."""
    provs = catalog.get("providers") or {}
    # provider key candidates: "zai", "custom:labbox" -> "labbox", registry ids
    cands = []
    with contextlib.suppress(Exception):
        p = str(provider or "").strip().lower()
        cands = [p, p.removeprefix("custom:")]
    for pid in cands:
        pnode = provs.get(pid)
        if not isinstance(pnode, dict):
            continue
        models = pnode.get("models") or {}
        if model in models and isinstance(models[model], dict):
            w = models[model].get("context_length") or models[model].get("limit") or 0
            with contextlib.suppress(Exception):
                if int(w) > 0:
                    return int(w)
        # substring: "glm-5.3" matches "glm-5.3-..." ids; also search across
        # ALL providers when the provider itself isn't in the catalog
        for mid, m in models.items():
            if isinstance(m, dict) and model and model.lower() in str(mid).lower():
                w = m.get("context_length") or m.get("limit") or 0
                with contextlib.suppress(Exception):
                    if int(w) > 0:
                        return int(w)
    # cross-provider search (openrouter-style model ids like "openai/gpt-4o")
    if "/" in model:
        _prov, _m = model.split("/", 1)
        return _lookup_in_catalog(catalog, _prov, _m.strip())
    return 0


def resolve_context_window(provider: str, model: str, config: dict | None = None) -> int:
    """Context window in TOKENS for the active provider/model. Never raises;
    every unknown -> 1M (the fallback contract)."""
    cfg = config or {}
    # 1. explicit operator override (config.json) — wins over everything
    with contextlib.suppress(Exception):
        w = int(cfg.get("context_window") or 0)
        if w > 0:
            return w
    # 2. models.dev (cached/fetched)
    model_id = str(model or "").strip()
    if model_id:
        with contextlib.suppress(Exception):
            catalog = _catalog()
            if catalog:
                w = _lookup_in_catalog(catalog, provider, model_id)
                if w > 0:
                    return w
    # 3. the fallback — assume 1M
    return DEFAULT_CONTEXT_WINDOW


def window_chars(provider: str, model: str, config: dict | None = None) -> int:
    """Context window in CHARS (÷4 token estimate) — the unit the prompt
    budgets speak."""
    return resolve_context_window(provider, model, config) * CHARS_PER_TOKEN


def window_status(provider: str, model: str, config: dict | None = None) -> dict:
    """Diagnostics for `suijin doctor`: the resolved window + its source."""
    cfg = config or {}
    with contextlib.suppress(Exception):
        w = int(cfg.get("context_window") or 0)
        if w > 0:
            return {"window_tokens": w, "source": "config override"}
    with contextlib.suppress(Exception):
        catalog = _load_cached() or _catalog()
        if catalog and model and _lookup_in_catalog(catalog, provider, str(model)) > 0:
            return {
                "window_tokens": _lookup_in_catalog(catalog, provider, str(model)),
                "source": "models.dev",
            }
    return {"window_tokens": DEFAULT_CONTEXT_WINDOW, "source": "fallback (1M assumed)"}
