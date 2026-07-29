"""
Productivity-based loop detection — zero-token heuristics.

Replaces the old keyword-based drift detection with LLM-emitted verdicts
that classify every tool call into productivity buckets:

    new_info      — call revealed something new
    confirmation  — already suspected, confirmed
    no_progress   — succeeded but yielded nothing usable
    blocked       — WAF, 403, captcha, rate limit
    duplicate     — output identical to a recent call

Ported from redamon/agentic/orchestrator_helpers/productivity.py.
"""
from __future__ import annotations

# Deploy config and test data — defined inline
DEPLOY_ENV = {"is_macos": True, "is_linux": False, "is_docker": False, "is_dev": True}
MIN_REQUIREMENTS = {"python_version": (3, 10), "ram_mb": 2048, "disk_mb": 5120, "cpu_cores": 2}
def check_min_requirements() -> dict:
    import sys, platform
    return {"python_ok": sys.version_info >= (3, 10), "python_version": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": platform.platform()}
def get_deploy_port(service: str) -> int:
    ports = {"lab_cloudboard_main": 5800, "lab_devops_dashboard": 5700}
    return ports.get(service, 0)
INTEGRATION_TEST_CONFIG = {"lab_ports": [5700, 5800, 5801, 5802], "agent_max_iterations": 20, "expected_min_flags": 1}
EXPECTED_TOOLS = ["execute_terminal", "http_request", "nmap", "gobuster", "sqlmap", "ffuf", "hydra", "nikto", "curl"]
def generate_empty_state() -> dict:
    return {"current_phase": "informational", "messages": [], "findings": [], "flags_found": [], "knowledge_graph": {}, "iteration": 0, "total_cost_usd": 0.0, "todo_list": [], "jobs": {}}

import hashlib
import json
import re
from typing import Optional


def _normalize_args_pattern(tool_name: str, tool_args: dict) -> str:
    """Generalize tool args to a 'shape' so /order/300500 and /order/300600
    collapse into the same pattern."""
    try:
        raw = json.dumps(tool_args or {}, sort_keys=True, ensure_ascii=False)
    except Exception:
        raw = str(tool_args or {})
    normalized = re.sub(r"\b\d+\b", "<int>", raw)
    normalized = re.sub(r"\b[a-f0-9]{8,}\b", "<hex>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b\d+\.\d+\.\d+\.\d+\b", "<ip>", normalized)
    normalized = re.sub(r"=[^&\"'\s]+", "=<val>", normalized)
    return f"{tool_name or '?'}::{normalized[:160]}"


def _output_fingerprint(step: dict) -> str:
    """Stable 8-hex fingerprint of response body, normalized for trivial diffs."""
    raw = (step.get("tool_output") or "")[:8000]
    normalized = re.sub(r"\s+", " ", raw).strip()
    normalized = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.\-+Z]+", "<ts>", normalized)
    normalized = re.sub(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
        "<uuid>", normalized,
    )
    normalized = re.sub(r"\b\d{10,}\b", "<num>", normalized)
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:8]


def _read_productivity(step: dict) -> dict:
    if not step:
        return {}
    top = step.get("productivity")
    if isinstance(top, dict) and top:
        return top
    nested = step.get("output_analysis")
    if isinstance(nested, dict):
        p = nested.get("productivity") or {}
        if isinstance(p, dict):
            return p
    return {}


def is_unproductive(step: dict) -> bool:
    """Read the LLM's productivity verdict for this step."""
    p = _read_productivity(step)
    if not p:
        return False
    if p.get("verdict") == "diagnostic_progress":
        return False
    if p.get("verdict") in ("no_progress", "duplicate", "blocked"):
        return True
    if p.get("new_information_gained") is False:
        return True
    return False


def audit_productivity_claim(
    productivity: dict,
    extracted_info: dict,
    actionable_findings: list,
    findings_grew: bool,
) -> Optional[str]:
    """Cross-check the LLM's productivity claim against actual state delta."""
    if not productivity:
        return None

    verdict = productivity.get("verdict")
    claims_new = productivity.get("new_information_gained", False)

    extracted_any = any(
        (extracted_info or {}).get(k)
        for k in ("ports", "services", "technologies",
                  "vulnerabilities", "credentials", "sessions")
    )
    state_grew = bool(findings_grew or extracted_any or actionable_findings)

    if claims_new and not state_grew:
        return ("Claimed new_information_gained=true but no chain finding was "
                "appended, no extracted_info was populated.")
    if verdict == "new_info" and not state_grew:
        return "Verdict='new_info' but the engagement state did not grow."
    if verdict == "diagnostic_progress" and not (productivity.get("what_was_new") or "").strip():
        return ("Verdict='diagnostic_progress' but what_was_new is empty.")
    return None


def downgrade_verdict_to_no_progress(productivity: dict, reason: str) -> dict:
    """Return a copy with verdict downgraded to 'no_progress'."""
    if not productivity:
        return {
            "verdict": "no_progress",
            "new_information_gained": False,
            "what_was_new": "",
            "should_repeat_similar_call": False,
            "rationale": "",
            "_original_verdict": None,
            "_downgrade_reason": reason,
        }
    out = dict(productivity)
    out["_original_verdict"] = out.get("verdict")
    out["verdict"] = "no_progress"
    out["new_information_gained"] = False
    out["_downgrade_reason"] = reason
    return out


def detect_uniform_response_anomaly(
    execution_trace: list,
    *,
    window: int = 8,
    min_count: int = 5,
    size_tolerance: int = 32,
    duration_threshold_ms: int = 50,
) -> Optional[str]:
    """Detect a 'uniform response cliff' — streak of identical-sized fast failures."""
    if len(execution_trace) < min_count:
        return None
    recent = execution_trace[-window:]
    failures = [
        s for s in recent
        if isinstance(s, dict)
        and not s.get("success", True)
    ]
    if len(failures) < min_count:
        return None

    sizes = [len(s.get("tool_output") or "") for s in failures]
    durations = [s.get("duration_ms", 0) for s in failures]
    avg_size = sum(sizes) / len(sizes)

    all_same_size = all(abs(s - avg_size) <= size_tolerance for s in sizes)
    all_fast = all(d <= duration_threshold_ms for d in durations)

    if all_same_size and all_fast:
        return (
            f"Uniform response anomaly: {len(failures)} recent failures with "
            f"near-identical output size (~{int(avg_size)} chars) and sub-"
            f"{duration_threshold_ms}ms duration. Target may be rejecting all "
            f"input at the edge. Switch approach or pivot target surface."
        )
    return None


# ---------------------------------------------------------------------------
# Axis-based tracking — prevents the agent from hammering the same pattern
# ---------------------------------------------------------------------------

def axis_key(tool_name: str, tool_args: dict) -> str:
    """Derive a stable axis key from a tool call's normalized shape."""
    return _normalize_args_pattern(tool_name, tool_args)


def extract_axis(step: dict) -> Optional[str]:
    """Extract the axis key from a step dict."""
    tn = step.get("tool_name", "")
    ta = step.get("tool_args") or {}
    return axis_key(tn, ta)


def record_axis_attempt(tested_axes: dict, axis: str, success: bool) -> dict:
    """Record an axis attempt and return updated dict."""
    entry = tested_axes.get(axis, {"attempts": 0, "failures": 0})
    entry["attempts"] = entry.get("attempts", 0) + 1
    if not success:
        entry["failures"] = entry.get("failures", 0) + 1
    tested_axes[axis] = entry
    return tested_axes


def axis_unproductive_count(tested_axes: dict) -> int:
    """Count axes with >=3 attempts and 100% failure rate."""
    return sum(
        1 for v in (tested_axes or {}).values()
        if v.get("attempts", 0) >= 3 and v.get("attempts", 0) == v.get("failures", 0)
    )


def priority_order_jaccard(current_todos: list, previous_todos: list) -> float:
    """Jaccard similarity of current vs previous todo priorities — high
    similarity across turns with no progress = spinning wheels."""
    curr = {t.get("description", "")[:80] for t in (current_todos or [])}
    prev = {t.get("description", "")[:80] for t in (previous_todos or [])}
    if not curr and not prev:
        return 1.0
    if not curr or not prev:
        return 0.0
    return len(curr & prev) / len(curr | prev)


# ---------------------------------------------------------------------------
# State growth detection
# ---------------------------------------------------------------------------

def detect_state_growth(before: dict, after: dict) -> bool:
    """True if any list-typed field in target_info grew."""
    b = (before or {}).get("target_info") or {}
    a = (after or {}).get("target_info") or {}
    for key in ("ports", "services", "technologies", "vulnerabilities",
                "credentials", "sessions", "subdomains", "endpoints"):
        if len(a.get(key, []) or []) > len(b.get(key, []) or []):
            return True
    return False


def detect_chain_advance(execution_trace: list, window: int = 6) -> bool:
    """True if any recent step has new_info verdict."""
    for step in execution_trace[-window:]:
        p = _read_productivity(step)
        if p.get("verdict") == "new_info":
            return True
    return False


def detect_diagnostic_progress(execution_trace: list, window: int = 3) -> bool:
    """True if any recent step has diagnostic_progress verdict (debugging a
    correct-but-failing approach — real progress, not a stall)."""
    for step in execution_trace[-window:]:
        p = _read_productivity(step)
        if p.get("verdict") == "diagnostic_progress":
            return True
    return False


def update_stall_counters(state: dict, iteration: int) -> dict:
    """Update _iterations_since_state_grew counter. Call after each turn."""
    grew = state.get("_state_grew_this_turn", False)
    chain_adv = state.get("_chain_advanced_this_turn", False)
    diag_prog = state.get("_diagnostic_progress_this_turn", False)

    prev_grew = state.get("_iterations_since_state_grew", 0)
    prev_chain = state.get("_iterations_since_chain_advance", 0)

    return {
        "_iterations_since_state_grew": 0 if grew else prev_grew + 1,
        "_iterations_since_chain_advance": 0 if (chain_adv or diag_prog) else prev_chain + 1,
    }


def compute_productivity_score(
    execution_trace: list,
    tested_axes: dict,
    iterations_since_state_grew: int,
    iteration: int,
    max_iterations: int,
    phase: str,
    window: int = 6,
    iterations_since_chain_advance: int = 0,
    novelty_saturation_grace: int = 3,
) -> dict:
    """Compute a numeric productivity score (0-10, higher = more stalled).

    Components:
    - unproductive_streak: how many consecutive unproductive steps
    - axis_exhaustion: how many axes are 100% failure after 3+ attempts
    - state_stagnation: how long since state last grew
    - chain_stagnation: how long since chain last advanced (new_info/diag)
    - phase_penalty: extra if still in informational after budget threshold
    - novelty_saturation: extra if growing but no chain advance (pure map growth)
    """
    recent = execution_trace[-window:] if execution_trace else []

    # 1. Unproductive streak (0-3)
    streak = 0
    for step in reversed(recent):
        if is_unproductive(step):
            streak += 1
        else:
            break
    unproductive_score = min(streak, 3)

    # 2. Axis exhaustion (0-3)
    axes_dead = axis_unproductive_count(tested_axes)
    axis_score = min(axes_dead, 3)

    # 3. State stagnation (0-2)
    stagnation_score = min(iterations_since_state_grew // 5, 2)

    # 4. Chain-advance stagnation (0-1)
    chain_score = 1 if iterations_since_chain_advance >= 4 else 0

    # 5. Phase penalty (0-1): informational phase beyond budget
    phase_score = 0
    if phase == "informational" and iteration > max_iterations * 0.6:
        phase_score = 1

    # 6. Novelty saturation (0-1): growing state but never advancing chain
    novelty_score = 0
    if iterations_since_chain_advance > novelty_saturation_grace and iterations_since_state_grew < iterations_since_chain_advance:
        novelty_score = min(1, (iterations_since_chain_advance - novelty_saturation_grace) / 3)

    total = unproductive_score + axis_score + stagnation_score + chain_score + phase_score + novelty_score

    return {
        "score": round(total, 1),
        "components": {
            "unproductive_streak": unproductive_score,
            "axis_exhaustion": axis_score,
            "state_stagnation": stagnation_score,
            "chain_stagnation": chain_score,
            "phase_penalty": phase_score,
            "novelty_saturation": round(novelty_score, 1),
        },
        "streak": streak,
        "axes_dead": axes_dead,
        "iterations_since_grew": iterations_since_state_grew,
        "iterations_since_chain": iterations_since_chain_advance,
    }


def tier_for_score(
    score: float,
    hint_threshold: float = 3.0,
    deepthink_threshold: float = 5.0,
    require_pivot_threshold: float = 7.0,
    block_threshold: float = 9.0,
) -> str:
    """Map a productivity score to a tier label."""
    if score >= block_threshold:
        return "critical"
    if score >= require_pivot_threshold:
        return "red"
    if score >= deepthink_threshold:
        return "orange"
    if score >= hint_threshold:
        return "yellow"
    return "green"
