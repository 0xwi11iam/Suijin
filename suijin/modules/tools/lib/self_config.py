"""adjust_config — the agent's self-service configuration tool.

The agent tunes ITS OWN run within an allowlist instead of stalling or
asking the operator. Two seams are applied together (the audit's map):

  1. DISK — write config.json: the provider family (provider, models,
     zai_endpoint, temperature, max_tokens_per_request, fallback chain)
     and stealth are re-read from disk on EVERY LLM call, so disk edits
     are live on the next call.
  2. IN-MEMORY — mutate the live engagement dict (registered here by
     the redteamer at engagement start): the mode governor and cost
     governor re-read it every think turn, so posture/cost changes are
     live on the next turn.

Forbidden forever (the guardrails): cost caps, stealth, safety modes
(mode_hitl/mode_guardrail), proxy, scope/authorization anything — the
operator owns those. Every change is audited via write_note and returns
exactly what went live.
"""

from __future__ import annotations

import contextlib
import json

# the live engagement config dict — run_red_team_async stores the REAL
# object here (by reference, never copied): in-place mutations flow to the
# governor/budget guard, which re-read the same dict every think turn.
_HOLDER: dict = {"cfg": {}}

# ── the allowlist: key → (validator, description) ──────────────────────
_PROVIDERS = None  # lazy: known provider keys


def _known_providers() -> set[str]:
    global _PROVIDERS
    if _PROVIDERS is None:
        base = {"deepseek", "zai", "gemini", "anthropic", "amd", "huggingface"}
        with contextlib.suppress(Exception):
            from suijin.modules.providers.lib.registry import PROVIDER_REGISTRY

            base |= set(PROVIDER_REGISTRY)
        _PROVIDERS = base
    return _PROVIDERS


def _v_posture(v):
    v = str(v).lower()
    return (v in ("recon", "assertive"), v)


def _v_float(lo, hi):
    def _v(x):
        try:
            x = float(x)
            return (lo <= x <= hi, x)
        except Exception:  # noqa: BLE001
            return (False, x)

    return _v


def _v_int(lo, hi):
    def _v(x):
        try:
            x = int(x)
            return (lo <= x <= hi, x)
        except Exception:  # noqa: BLE001
            return (False, x)

    return _v


def _v_str_list(allowed=None):
    def _v(x):
        if isinstance(x, str):
            x = [p.strip() for p in x.split(",") if p.strip()]
        if not isinstance(x, list) or not all(isinstance(p, str) for p in x):
            return (False, x)
        if allowed is not None:
            bad = [p for p in x if p.lower() not in allowed]
            if bad:
                return (False, x)
        return (True, [p.lower() for p in x])

    return _v


def _v_model(v):
    return (bool(str(v).strip()), str(v).strip())


ALLOWLIST = {
    "posture": (_v_posture, "recon | assertive — the mode-governor dial (live next turn)"),
    "temperature": (_v_float(0.0, 2.0), "LLM sampling temperature (live next call)"),
    "max_tokens_per_request": (_v_int(100, 200000), "max tokens per LLM call (live next call)"),
    "provider": (
        lambda v: (str(v).lower() in _known_providers(), str(v).lower()),
        "primary LLM provider (live next call)",
    ),
    "fallback_providers": (_v_str_list(), "failover chain after the primary (live next call)"),
    "zai_model": (_v_model, "zai model id"),
    "deepseek_model": (_v_model, "deepseek model id"),
    "gemini_model": (_v_model, "gemini model id"),
    "anthropic_model": (_v_model, "anthropic model id"),
}

FORBIDDEN = (
    "cost_alert_usd",
    "cost_budget_usd",
    "cost_hard_cap_usd",
    "max_cost_usd",
    "stealth",
    "mode_hitl",
    "mode_guardrail",
    "proxy_url",
    "max_iterations",
)


def set_live_config(cfg: dict) -> None:
    """Called by run_red_team_async — stores THE engagement dict by
    reference so adjust_config's in-place mutations are what the
    governor/budget guard read on the next think turn."""
    if isinstance(cfg, dict):
        _HOLDER["cfg"] = cfg


def _config_path():
    from pathlib import Path

    from suijin.modules.platform.lib.config_loader import CONFIG_PATH

    return Path(CONFIG_PATH)


def adjust_config(**changes) -> str:
    """No args → show the effective config. With args → validate against
    the allowlist, apply BOTH seams, audit, and report what went live."""
    try:
        if not changes:
            return _show()
        applied: list[str] = []
        rejected: list[str] = []
        disk: dict = {}
        with contextlib.suppress(Exception):
            disk = json.loads(_config_path().read_text())

        for key, value in changes.items():
            if key in FORBIDDEN:
                rejected.append(f"{key}: operator-only (safety/cost/scope)")
                continue
            spec = ALLOWLIST.get(key)
            if spec is None:
                rejected.append(f"{key}: not in the agent allowlist ({', '.join(sorted(ALLOWLIST))})")
                continue
            ok, normalized = spec[0](value)
            if not ok:
                rejected.append(f"{key}: invalid value {value!r} — expected {spec[1]}")
                continue
            # seam 1: the live engagement dict ITSELF (governor + budget
            # re-read this object every think turn)
            _HOLDER["cfg"][key] = normalized
            # seam 2: disk (provider family + stealth re-read per LLM call)
            disk[key] = normalized
            applied.append(f"{key}={normalized!r} ({spec[1].split('—')[0].strip()})")

        if applied:
            with contextlib.suppress(Exception):
                _config_path().write_text(json.dumps(disk, indent=4))
            with contextlib.suppress(Exception):
                from suijin.modules.tools.lib.intel import write_note

                write_note("config", f"agent self-adjusted: {', '.join(a.split(' (')[0] for a in applied)}")
        out = []
        if applied:
            out.append("APPLIED (live):")
            out.extend(f"  ✓ {a}" for a in applied)
        if rejected:
            out.append("REJECTED:")
            out.extend(f"  ✗ {r}" for r in rejected)
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001 — tools return strings, never raise
        return f"Error: adjust_config failed: {e}"


def _show() -> str:
    from suijin.modules.platform.lib.config_loader import load_config

    cfg = dict(load_config())
    live = _HOLDER.get("cfg") or {}
    if live:
        cfg.update({k: v for k, v in live.items() if not k.startswith("_")})
    keys = [
        "provider",
        "posture",
        "temperature",
        "max_tokens_per_request",
        "fallback_providers",
        "zai_model",
        "deepseek_model",
        "zai_endpoint",
    ]
    lines = ["effective config (agent-mutable keys marked *):"]
    lines += [
        f"  {'*' if k in ALLOWLIST else ' '}{k} = {cfg.get(k, '(unset)')!r}" for k in keys if cfg.get(k) is not None
    ]
    lines.append(f"  adjustable keys: {', '.join(sorted(ALLOWLIST))}")
    lines.append("operator-only: cost caps, stealth, safety modes, proxy, iteration budget")
    return "\n".join(lines)
