"""Feature builders — the fixed digest every question sees.

The digest is SMALLER than an LLM context on purpose: a classifier's
judgment lives in the right features, not more text. Each builder is
pure (state+trace in, floats/ints out) so shadow mode can log the exact
input that produced a decision and the corpus is reproducible.
"""

from __future__ import annotations

import hashlib
from collections import Counter

RECON_TOOLS = {"search_kb", "web_search", "dns_lookup", "http_request", "source_map_probe"}
EXPLOIT_MARKERS = ("exploit", "payload", "catalog", "poc", "inject", "attack")


def _recent(trace: list, n: int = 6) -> list[dict]:
    return [t for t in (trace or [])[-n:] if isinstance(t, dict)]


def build_features(state: dict, trace: list) -> dict:
    """The shared digest: supervisor/drifter features over state+trace."""
    state = state or {}
    recent = _recent(trace)
    iterations = int(state.get("current_iteration") or 0)
    phase = str(state.get("current_phase") or "recon")

    # iterations since the last confirmed finding
    findings = [f for f in (state.get("findings") or []) if isinstance(f, dict)]
    iterations_since_finding = iterations - max((int(f.get("iteration") or 0) for f in findings), default=0)

    # repeat pressure: identical tool+key-arg shapes among recent steps
    shapes = [(str(t.get("tool_name") or "?"), str(t.get("args", ""))[:60]) for t in recent]
    shape_counts = Counter(shapes)
    repeat_pressure = 0.0
    if recent:
        repeats = sum(c - 1 for c in shape_counts.values())
        repeat_pressure = min(1.0, repeats / max(1, len(recent)))

    # phase/tool mismatch: recon-tool usage during exploitation+ phases
    phase_tool_mismatch = 0
    if phase in ("exploitation", "post_exploitation"):
        phase_tool_mismatch = sum(1 for t in recent if str(t.get("tool_name") or "") in RECON_TOOLS)

    # consecutive failures at the tail
    tool_fail_cluster = 0
    for t in reversed(recent):
        if t.get("success") is False:
            tool_fail_cluster += 1
        else:
            break

    # unexploited chain candidates: confirmed findings never cataloged
    chain_candidates = sum(
        1
        for f in findings
        if str(f.get("status", "")).lower() in ("confirmed", "reproduced") and not f.get("cataloged")
    )

    # objective/action digests for the drifter: tool-class mix
    objective_digest = hashlib.sha1(
        " ".join(sorted(set(str(state.get("original_objective") or "").lower().split())))[:400].encode()
    ).hexdigest()[:10]
    action_mix = Counter("recon" if str(t.get("tool_name") or "") in RECON_TOOLS else "act" for t in recent)
    recent_actions_digest = f"{action_mix.get('recon', 0)}/{len(recent) or 1}"

    # cost rate (trailing), guarded — providers may not report dollars
    cost_rate_usd = 0.0
    try:
        from suijin.modules.providers.lib import get_usage

        u = get_usage()
        cost_rate_usd = round(float(u.get("est_cost_usd", 0.0)) / max(1, iterations), 5)
    except Exception:  # noqa: BLE001 — features never break a turn
        pass

    log = [e for e in (state.get("_supervisor_log") or []) if isinstance(e, dict)]
    unheeded_count = sum(1 for e in log if e.get("heeded") is False)
    last_intervention_turns_ago = iterations - int(log[-1].get("turn") or 0) if log else 999

    return {
        "phase": phase,
        "iteration": iterations,
        "iterations_since_finding": max(0, iterations_since_finding),
        "repeat_pressure": round(repeat_pressure, 3),
        "phase_tool_mismatch": phase_tool_mismatch,
        "tool_fail_cluster": tool_fail_cluster,
        "chain_candidates": chain_candidates,
        "cost_rate_usd": cost_rate_usd,
        "drift_score": float(state.get("_drift_score") or 0.0),
        "unheeded_count": unheeded_count,
        "objective_digest": objective_digest,
        "recent_actions_digest": recent_actions_digest,
        "last_intervention_turns_ago": last_intervention_turns_ago,
        "heeded_last": (log[-1].get("heeded") if log else None),
        "speakworthy_facts": 0,  # filled by the supervisor seam (facts_brief ran there)
    }


def triage_features(tool_output: str, status_code: int = 200, elapsed_ms: float = 0.0) -> dict:
    """oracle.triage digest: shape-of-response, never the body itself."""
    import re

    body = str(tool_output or "")
    error_markers = len(re.findall(r"\b(traceback|error|exception|warning|fatal|syntax)\b", body, re.I))
    reflect = len(re.findall(r"[<>]|\bSELECT\b|\$\{|\{\{|\broot:\b", body, re.I))
    return {
        "status_code": int(status_code),
        "body_len_delta": 0.0,  # seam fills vs the rolling median when present
        "elapsed_ms": float(elapsed_ms),
        "error_markers": min(error_markers, 20),
        "reflect_indicators": min(reflect, 20),
    }


def adjudicate_features(hypothesis: dict, control_difference: float = 0.0) -> dict:
    """oracle.adjudicate digest per hypothesis."""
    h = hypothesis or {}
    return {
        "hypothesis_class": str(h.get("class") or h.get("rule") or "?")[:30],
        "evidence_markers": min(int(h.get("evidence_count") or len(h.get("evidence") or [])), 10),
        "control_difference": round(max(0.0, min(1.0, float(control_difference))), 3),
        "claimed_confidence": round(max(0.0, min(1.0, float(h.get("confidence") or 0.5))), 3),
    }
