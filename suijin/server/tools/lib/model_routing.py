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
    """
    cfg = _routing_config()
    if not cfg:
        return None
    key = "worker" if task in ("worker", "triage") else "planner"
    return str(cfg.get(key) or "") or None


def should_escalate(response_length: int, has_confirmed: bool = False) -> bool:
    """Should this observation go to the planner model instead of the
    worker model? Long responses and CONFIRMED results deserve frontier
    reasoning; everything else stays cheap."""
    cfg = _routing_config()
    if not cfg:
        return has_confirmed
    threshold = int(cfg.get("classify_threshold") or 2000)
    return has_confirmed or response_length > threshold


def apply_routing(cfg: dict, task: str = "worker") -> dict:
    """Mutate a provider config dict for the task tier. The worker loop
    calls this before generate()."""
    model = model_for(task)
    if model:
        provider = str(cfg.get("provider") or "")
        cfg[f"{provider}_model"] = model
    return cfg
