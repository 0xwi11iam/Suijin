"""Tiered model routing — frontier for thinking, flash for volume.

The review's cost point: Suijin burns frontier tokens on everything.
This router splits the work:

  PLANNER   exploit synthesis, chain reasoning, finding analysis
            → the configured model (glm-5.3)
  WORKER    recon triage, JS bundle parsing, observation matching,
            header comparison → a fast/cheap model (config)

The worker loop already sets intelligence=medium; this routes the MODEL
ITSELF per task type. Config:

  model_routing:
    planner: glm-5.3           # the frontier
    worker: glm-5.3-flash      # the cheap volume model
    classify_threshold: 2000   # responses longer than this go to planner
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path


def _routing_config() -> dict:
    """Model routing config — reads BOTH the nested dict (power users)
    and the flat settings keys (the settings TUI writes):
      routing_worker_model / routing_planner_model /
      routing_escalate_chars → model_routing.{worker,planner,classify_threshold}"""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        cfg = load_config() or {}
        nested = dict(cfg.get("model_routing") or {})
        if cfg.get("routing_worker_model"):
            nested.setdefault("worker", str(cfg["routing_worker_model"]))
        if cfg.get("routing_planner_model"):
            nested.setdefault("planner", str(cfg["routing_planner_model"]))
        if cfg.get("routing_escalate_chars"):
            nested.setdefault("classify_threshold", int(cfg["routing_escalate_chars"]))
        return nested
    return {}


def model_for(task: str, provider: str = "") -> str | None:
    """The model id for a task type. None = keep the configured default.

    task: 'planner' | 'worker' | 'triage' (alias for worker)
          | 'tier:leader' | 'tier:supervisor' | 'tier:worker'
          (structured mode — cascade m3→m2→m1, env override wins)
    """
    if task.startswith("tier:"):
        return _tier_model(task.split(":", 1)[1])
    cfg = _routing_config()
    if not cfg:
        return None
    key = "worker" if task in ("worker", "triage") else "planner"
    return str(cfg.get(key) or "") or None


def _tier_model(tier: str) -> str | None:
    """Structured-mode tier model: env override (set at spawn) wins,
    then config model_1/2/3 with the fallback cascade m3→m2→m1."""
    with contextlib.suppress(Exception):
        env_model = os.environ.get("SUIJIN_TIER_MODEL")
        if env_model:
            return env_model
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        cfg = load_config() or {}
        m1 = str(cfg.get("model_1") or "")
        m2 = str(cfg.get("model_2") or m1)
        m3 = str(cfg.get("model_3") or m2)
        return {"leader": m1, "supervisor": m2, "worker": m3}.get(tier) or None
    return None


def should_escalate(response_length: int, has_confirmed: bool = False) -> bool:
    """Should this observation go to the planner model instead of the
    worker model? Long responses and CONFIRMED results deserve frontier
    reasoning; everything else stays cheap."""
    cfg = _routing_config()
    if not cfg:
        return has_confirmed
    threshold = int(cfg.get("classify_threshold") or 2000)
    return has_confirmed or response_length > threshold


def _split_provider_model(value: str) -> tuple[str, str]:
    """'provider:model-id' → (provider, model-id) when the prefix is a
    known provider (numes, openrouter, zai, ...). Model ids may contain
    colons (openrouter 'z-ai/glm-5.3:free') — only a registry prefix wins."""
    if ":" not in value:
        return "", value
    head, rest = value.split(":", 1)
    try:
        from suijin.server.providers.lib.registry import PROVIDER_REGISTRY

        if head in PROVIDER_REGISTRY or head == "custom":
            return head, rest
    except Exception:  # noqa: BLE001
        pass
    return "", value


def apply_routing(cfg: dict, task: str = "worker") -> dict:
    """Mutate a provider config dict for the task tier. The worker loop
    calls this before generate(). A 'provider:model' value routes that
    tier at the OTHER provider (any connected model, any slot).

    A BARE model id belongs to the provider that lists it. Until 2026-11-28 it was sent to the
    active provider whatever that was: with Z.ai active and a Numes worker model, every agent
    call answered `400 Unknown Model` while the leader, on the same provider, worked."""
    model = model_for(task)
    if not model:
        return cfg
    provider, wire_model = _split_provider_model(model)
    if provider:
        cfg = dict(cfg)
        cfg["provider"] = provider
        cfg[f"{provider}_model"] = wire_model
        cfg["model_1"] = wire_model
        return cfg
    active = str(cfg.get("provider") or "")
    owner = _owner_in_config(cfg, model, active) or owner_of(model, active)
    if owner and owner != active:
        cfg = dict(cfg)
        cfg["provider"] = owner
    cfg[f"{owner or active}_model"] = model
    return cfg


def _registered_providers() -> tuple[str, ...]:
    try:
        from suijin.server.providers.lib.registry import PROVIDER_REGISTRY

        return tuple(PROVIDER_REGISTRY)
    except Exception:  # noqa: BLE001
        return ()


def _owner_in_config(cfg: dict, model: str, active: str) -> str:
    """A ``<provider>_model`` setting in this very config already names the model: that provider
    is its owner. The active provider wins a tie — no switch is needed then."""
    model = model.strip()
    suffix = "_model"
    providers = [
        key[: -len(suffix)]
        for key, value in (cfg or {}).items()
        if key.endswith(suffix) and str(value).strip() == model
    ]
    if active in providers:
        return active
    known = _registered_providers()
    return next((provider for provider in providers if not known or provider in known), "")


# ── which provider lists which model ─────────────────────────────────────────────────────────
# The console sees every configured provider's model list when it builds the model picker. It
# records that here once per complete listing, and every process that routes a call — console,
# agent, TUI — reads it. No network, no key, nothing secret: provider names and model ids.


def model_owners_path() -> Path:
    from suijin.modules.platform.lib.workspace import caches_dir  # function-local (boundary law)

    return caches_dir() / "model-owners.json"


def remember_model_owners(listings: dict[str, list[str]]) -> None:
    """Record one COMPLETE listing — every configured provider and the models it offers. It
    replaces the previous record. Never raises."""
    owners: dict[str, list[str]] = {}
    for provider, models in (listings or {}).items():
        for model in models or []:
            model = str(model).strip()
            if model:
                providers = owners.setdefault(model, [])
                if provider not in providers:
                    providers.append(str(provider))
    with contextlib.suppress(OSError):
        path = model_owners_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(owners), encoding="utf-8")


def model_owners() -> dict[str, list[str]]:
    """model id → the providers whose last complete listing offered it."""
    with contextlib.suppress(Exception):
        data = json.loads(model_owners_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(model): [str(p) for p in providers]
                for model, providers in data.items()
                if isinstance(providers, list)
            }
    return {}


def owner_of(model: str, active: str) -> str:
    """The provider a bare model id is called on: the active one when it lists the model, else
    another one that does. '' when no listing knows the model — the active provider is used then,
    as before, and the error (if there is one) will say so."""
    providers = model_owners().get(str(model).strip(), [])
    if active in providers:
        return active
    return next((provider for provider in providers if provider), "")


def routing_doubt(cfg: dict, task: str) -> str:
    """Why a route may not work, in one sentence; '' when it looks sound. Call it BEFORE
    apply_routing, and carry the sentence into any provider error the call then raises."""
    model = model_for(task)
    if not model:
        return ""
    provider, _wire_model = _split_provider_model(model)
    if provider:
        return ""  # said explicitly
    active = str((cfg or {}).get("provider") or "")
    target = _owner_in_config(cfg, model, active)
    owners = model_owners()
    if not target:
        if not owners or model in owners:
            return ""  # no listing has ever been recorded, or a provider that is no longer configured lists it
        target = owner_of(model, active)
    if target:
        return ""  # a provider that lists it: the active one, or the one apply_routing switches to
    return f"{model} is not in the model list of {active}, the active provider; the call is being made there anyway"


def routing_note(cfg: dict, task: str) -> tuple[str, str]:
    """``(model, provider)`` a task is routed to, for logs and error messages. Read-only."""
    model = model_for(task)
    if not model:
        return "", str((cfg or {}).get("provider") or "")
    routed = apply_routing(dict(cfg or {}), task)
    return model, str(routed.get("provider") or "")
