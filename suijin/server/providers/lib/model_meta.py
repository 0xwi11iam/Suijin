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


def _window_from_entry(m: dict) -> int:
    """The context window from one models.dev entry, shape-aware:
    `context_length` (int, some providers), `limit` as the current catalog
    dict {"context": N, "output": M}, or `limit` as a legacy bare int.
    0 when absent — the caller falls back."""
    for key in ("context_length", "context"):
        with contextlib.suppress(Exception):
            w = int(m.get(key) or 0)
            if w > 0:
                return w
    lim = m.get("limit")
    if isinstance(lim, dict):
        with contextlib.suppress(Exception):
            w = int(lim.get("context") or 0)
            if w > 0:
                return w
    elif isinstance(lim, (int, float)):
        with contextlib.suppress(Exception):
            return int(lim)
    return 0


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
            w = _window_from_entry(models[model])
            if w > 0:
                return w
        # substring: "glm-5.3" matches "glm-5.3-..." ids; also search across
        # ALL providers when the provider itself isn't in the catalog
        for mid, m in models.items():
            if isinstance(m, dict) and model and model.lower() in str(mid).lower():
                w = _window_from_entry(m)
                if w > 0:
                    return w
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


def supported_effort_levels(provider: str, model: str, config: dict | None = None) -> list[str]:
    """The effort values this model accepts (models.dev reasoning_options),
    normalized to suijin's tier vocabulary. Empty when unsupported/unknown —
    callers keep their default behavior. Never raises."""
    with contextlib.suppress(Exception):
        catalog = _catalog()
        if not catalog or not model:
            return []
        node = _find_model_node(catalog, provider, str(model))
        if not node:
            return []
        for opt in node.get("reasoning_options") or []:
            if isinstance(opt, dict) and str(opt.get("type", "")).lower() == "effort":
                return [str(v).lower() for v in (opt.get("values") or []) if v]
    return []


def _find_model_node(catalog: dict, provider: str, model: str) -> dict | None:
    provs = catalog.get("providers") or {}
    for pid in (str(provider or "").strip().lower(), str(provider or "").strip().lower().removeprefix("custom:")):
        pnode = provs.get(pid)
        if not isinstance(pnode, dict):
            continue
        models = pnode.get("models") or {}
        if model in models and isinstance(models[model], dict):
            return models[model]
        for mid, m in models.items():
            if isinstance(m, dict) and model.lower() in str(mid).lower():
                return m
    if "/" in model:
        _p, _m = model.split("/", 1)
        return _find_model_node(catalog, _p, _m.strip())
    return None


# ── pricing (models.dev is the source of truth — nothing hardcoded) ──────


def _pricing_from_entry(m: dict) -> tuple[float, float] | None:
    """(input, output) $/1M from a models.dev entry's cost object. None
    when absent or zero (zero = bundled in a plan, not a real price)."""
    with contextlib.suppress(Exception):
        c = m.get("cost") or {}
        i = float(c.get("input") or 0)
        o = float(c.get("output") or 0)
        if i > 0 and o > 0:
            return (i, o)
    return None


def _lookup_pricing(catalog: dict, provider: str, model: str) -> tuple[float, float] | None:
    provs = catalog.get("providers") or {}
    p = str(provider or "").strip().lower()
    cands = [p, p.removeprefix("custom:")]
    for pid in cands:
        pnode = provs.get(pid)
        if not isinstance(pnode, dict):
            continue
        models = pnode.get("models") or {}
        # exact then substring model match
        for mid, m in models.items():
            if isinstance(m, dict) and mid.lower() == str(model).lower():
                pr = _pricing_from_entry(m)
                if pr:
                    return pr
        for mid, m in models.items():
            if isinstance(m, dict) and str(model).lower() in mid.lower():
                pr = _pricing_from_entry(m)
                if pr:
                    return pr
    # global scan: model ids are near-unique across providers — a bare
    # id like glm-5.3 resolves even when the caller's provider string is
    # generic. TWO passes: full-id equality, then BASE-id equality (after
    # any aggregator prefix, "deepseek-ai/X" == "x") so the same model
    # costs the same however it is listed.
    _m = str(model).lower()
    for pnode in provs.values():
        if not isinstance(pnode, dict):
            continue
        for mid, m in (pnode.get("models") or {}).items():
            if isinstance(m, dict) and mid.lower() == _m:
                pr = _pricing_from_entry(m)
                if pr:
                    return pr
    _base = _m.split("/")[-1]
    if _base != _m:
        for pnode in provs.values():
            if not isinstance(pnode, dict):
                continue
            for mid, m in (pnode.get("models") or {}).items():
                if isinstance(m, dict) and mid.lower().split("/")[-1] == _base:
                    pr = _pricing_from_entry(m)
                    if pr:
                        return pr
    # coding-plan rows price at 0 (credits) — the honest USD is the
    # PAY-AS-YOU-GO row of the same family: retry without the plan suffix
    if any(c.endswith("-coding-plan") for c in cands):
        for pid in (c.removesuffix("-coding-plan") for c in cands if c.endswith("-coding-plan")):
            pnode = provs.get(pid)
            if isinstance(pnode, dict):
                for mid, m in (pnode.get("models") or {}).items():
                    if isinstance(m, dict) and str(model).lower() in mid.lower():
                        pr = _pricing_from_entry(m)
                        if pr:
                            return pr
    return None


def resolve_pricing(provider: str, model: str, config: dict | None = None) -> tuple[float, float] | None:
    """(input, output) $/1M from the LIVE models.dev catalog (cached,
    7-day TTL — same seam as context windows). Operator-config pricing for
    custom providers wins first; None when the catalog has no real price
    (the caller's offline fallback applies and is labeled approximate).
    Never raises."""
    cfg = config or {}
    # 1. operator-declared custom provider pricing (their endpoint, their
    #    word — models.dev cannot know a LAN box)
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib.registry import active_pricing, resolve_custom_provider

        for _entry in cfg.get("custom_providers") or []:
            _name = str(_entry.get("name", "")).strip()
            _spec = resolve_custom_provider(_name, cfg) if _name else None
            if _spec and _spec.default_model and _spec.default_model.lower() in str(model or "").lower():
                _sel = str((_entry.get("pricing") or {}).get("selection", "auto"))
                _pair = active_pricing(_spec, _sel)
                if _pair:
                    return _pair
    # 2. models.dev (cached/fetched)
    model_id = str(model or "").strip()
    if model_id:
        with contextlib.suppress(Exception):
            catalog = _catalog()
            if catalog:
                pr = _lookup_pricing(catalog, provider, model_id)
                if pr:
                    return pr
    return None
