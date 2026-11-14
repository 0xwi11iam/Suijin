"""Transport guardrails — the network layer, not the prompt.

The review's sharpest point: scope enforcement lives in the prompt, and
a model can be talked out of a prompt. These hooks sit BELOW the model:
the HTTP session itself refuses out-of-scope and destructive requests,
no matter what the agent says.

Three rails (all config-driven, all overridable per engagement):
  SCOPE     allowlist regexes — a request to a non-matching host DROPS
            at the socket, the agent sees 'BLOCKED: out of scope'
  METHOD    destructive HTTP methods (PUT/DELETE/PATCH) on paths that
            don't match a mutation-allowlist get refused
  RATE      per-host token bucket enforced at the transport (the
            session layer, not the tool layer — boxed agents inherit
            it via the shared config mount)
"""

from __future__ import annotations

import contextlib
import re
import threading
import time


def _config() -> dict:
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        return load_config() or {}
    return {}


def scope_patterns() -> list[str]:
    """Host allowlist regexes. Empty = no scope restriction (the
    engagement's own authorization gate still applies at the policy
    layer — this is the network-level belt on top). Reads both
    guardrails.scope_hosts (nested) and guardrail_scope_hosts (flat
    settings key, comma-separated)."""
    cfg = _config()
    nested = [str(x) for x in (cfg.get("guardrails") or {}).get("scope_hosts", [])]
    flat = str(cfg.get("guardrail_scope_hosts") or "").strip()
    if flat:
        nested.extend(p.strip() for p in flat.split(",") if p.strip())
    return nested


def mutation_allow_patterns() -> list[str]:
    """Paths where PUT/DELETE/PATCH are permitted (e.g. api test
    endpoints you own). Empty = all mutations blocked."""
    return [str(x) for x in _config().get("guardrails", {}).get("mutation_allow_paths", [])]


# ── rate: per-host token bucket ─────────────────────────────────

_buckets: dict = {}
_bucket_lock = threading.Lock()


def _check_rate(host: str) -> float:
    """Seconds to wait (0 = go). Reads guardrail_rate_per_sec (flat
    settings key) or guardrails.rate_per_host_per_sec (nested). Default 5."""
    cfg = _config()
    rate = cfg.get("guardrail_rate_per_sec")
    if rate is None:
        rate = (cfg.get("guardrails") or {}).get("rate_per_host_per_sec", 5.0)
    if cfg <= 0:
        return 0.0
    with _bucket_lock:
        now = time.time()
        b = _buckets.setdefault(host, {"tokens": cfg, "last": now})
        refill = (now - b["last"]) * cfg
        b["tokens"] = min(cfg, b["tokens"] + refill)
        b["last"] = now
        if b["tokens"] >= 1.0:
            b["tokens"] -= 1.0
            return 0.0
        return (1.0 - b["tokens"]) / cfg


def guard(url: str, method: str = "GET") -> str | None:
    """The pre-flight check. Returns None (allow) or the BLOCK reason.
    Called by the transport layer BEFORE the socket opens."""
    with contextlib.suppress(Exception):
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if not host:
            return "BLOCKED: no host in URL"

        # RAIL 1 — scope
        patterns = scope_patterns()
        if patterns and not any(re.match(p, host) for p in patterns):
            return f"BLOCKED: {host} is outside the engagement scope (guardrails.scope_hosts)"

        # RAIL 2 — destructive methods
        if method.upper() in ("PUT", "DELETE", "PATCH"):
            allows = mutation_allow_patterns()
            path = urlparse(url).path or "/"
            if allows and not any(re.search(p, path) for p in allows):
                return f"BLOCKED: {method} on {path} — no mutation allowlist match (guardrails.mutation_allow_paths)"
            if not allows:
                return f"BLOCKED: {method} blocked by default — add the path to guardrails.mutation_allow_paths if intended"

        # RAIL 3 — rate
        wait = _check_rate(host)
        if wait > 0.5:  # sub-0.5s waits absorb inline; longer reports
            return f"THROTTLED: per-host rate reached — wait {wait:.1f}s (guardrails.rate_per_host_per_sec)"
        if wait > 0:
            time.sleep(wait)

    return None  # allow
