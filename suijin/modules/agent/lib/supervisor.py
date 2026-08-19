"""
Supervisor — lightweight pattern-based oversight for the agent loop.

Runs every N iterations (default 5). Analyzes recent execution trace
for problematic patterns and silently injects corrective guidance.

Pattern-based (no LLM calls) — zero cost, instant execution.
"""

from __future__ import annotations

import logging
from typing import Optional

# Phase transition config — defined inline
PHASE_TRANSITIONS = {
    "recon_to_scan": {"min_ports_discovered": 5, "min_services_identified": 3, "max_recon_iterations": 15},
    "scan_to_exploit": {"min_vulns_found": 1, "min_high_severity": 0, "max_scan_iterations": 20},
    "exploit_to_post": {"min_successful_exploits": 1, "min_flags_captured": 0, "max_exploit_iterations": 30},
}
PARALLEL_LIMITS = {
    "max_concurrent_scans": 4,
    "max_concurrent_exploits": 2,
    "max_concurrent_subagents": 3,
    "max_background_jobs": 8,
    "queue_timeout_seconds": 300,
}
RETRY_POLICY = {
    "max_retries": 3,
    "base_delay_seconds": 2,
    "max_delay_seconds": 60,
    "backoff_multiplier": 2.0,
    "jitter": True,
    "retry_on_status": [429, 500, 502, 503, 504],
}


def get_phase_config(phase: str) -> dict:
    for key, config in PHASE_TRANSITIONS.items():
        if phase.lower() in key:
            return config
    return {}


logger = logging.getLogger(__name__)

# Patterns that trigger supervisor intervention
# Each pattern: (name, detector_fn, guidance_template)


def _detect_repeating_tool(trace: list, threshold: int = 3) -> Optional[str]:
    """Detect if the same tool+args has been used 3+ times in a row."""
    if len(trace) < threshold:
        return None
    recent = trace[-threshold:]
    tools = [s.get("tool_name", "") for s in recent]
    if len(set(tools)) == 1 and tools[0]:
        return (
            f"You've called '{tools[0]}' {threshold} times in a row. "
            f"If it's not producing new results, STOP and try a DIFFERENT approach. "
            f"Switch to a different tool, different endpoint, or different attack vector entirely."
        )
    return None


def _detect_found_but_not_exploited(trace: list) -> Optional[str]:
    """Detect if a vulnerability was found but not followed up."""
    if len(trace) < 3:
        return None
    finding_keywords = [
        "SQLi",
        "SSTI",
        "XSS",
        "RCE",
        "SSRF",
        "IDOR",
        "injection",
        "vulnerability",
        "exposed",
        "leaked",
        "bypass",
        "flag",
        "command injection",
        "path traversal",
        "deserialization",
    ]
    # Find the index of the most recent finding
    finding_idx = -1
    for i in range(len(trace) - 1, -1, -1):
        thought = trace[i].get("thought", "")
        if any(kw.lower() in thought.lower() for kw in finding_keywords):
            finding_idx = i
            break
    if finding_idx < 0:
        return None
    # Check if tools AFTER the finding are exploitation tools
    exploit_tools = {"http_request", "execute_terminal", "sqlmap_scan", "deploy_subagent"}
    after_finding = trace[finding_idx + 1 :]
    if not after_finding:
        return None  # just found it this turn, give the agent a chance
    recent_after = [s.get("tool_name", "") for s in after_finding[-3:]]
    if not any(t in exploit_tools for t in recent_after):
        return (
            "You found a vulnerability but haven't exploited it yet. "
            "Stop doing recon/bookkeeping. TEST the vulnerability NOW. "
            "Use http_request with a payload, or execute_terminal with an exploit command."
        )
    return None


def _detect_bookkeeping_loop(trace: list, threshold: int = 4) -> Optional[str]:
    """Detect if the agent is stuck in a bookkeeping loop (notes, creds, job checks)."""
    if len(trace) < threshold:
        return None
    recent_tools = [s.get("tool_name", "") for s in trace[-threshold:]]
    bookkeeping = {
        "write_note",
        "creds_add",
        "job_list",
        "job_status",
        "job_output",
        "check_knowledge",
        "record_finding",
        "read_file",
        "web_search",
        "write_file",
        "search_cve",
    }
    if all(t in bookkeeping for t in recent_tools if t):
        return (
            f"You've spent {threshold} turns on bookkeeping without making progress. "
            f"STOP documenting. START exploiting. Run an actual attack tool NOW — "
            f"http_request with a payload, execute_terminal with an exploit, or "
            f"deploy_subagent for targeted attacks."
        )
    return None


def _detect_no_progress(trace: list, threshold: int = 5) -> Optional[str]:
    """Detect if no new information has been gained in N iterations."""
    if len(trace) < threshold:
        return None
    recent = trace[-threshold:]
    no_progress = all(
        s.get("productivity", {}).get("verdict") in ("no_progress", "blocked", "duplicate")
        or not s.get("success", True)
        for s in recent
    )
    if no_progress:
        return (
            f"No progress in {threshold} iterations. You may be stuck in a loop. "
            f"RADICALLY change your approach: try a completely different attack vector, "
            f"a different port, a different tool. If the target has no more surface area, "
            f"generate your report and complete."
        )
    return None


def _detect_subagents_failing(trace: list, threshold: int = 3) -> Optional[str]:
    """Detect if subagents keep returning no findings."""
    if len(trace) < threshold:
        return None
    subagent_attempts = 0
    for s in trace[-6:]:
        thought = s.get("thought", "")
        if "subagent" in thought.lower() and (
            "returned no" in thought.lower() or "failed" in thought.lower() or "partial" in thought.lower()
        ):
            subagent_attempts += 1
    if subagent_attempts >= threshold:
        return (
            f"Subagents have failed {subagent_attempts} times. They are NOT working for this task. "
            f"Stop deploying subagents. Execute the task YOURSELF directly with http_request or "
            f"execute_terminal. You are more capable than a subagent."
        )
    return None


def _detect_missed_flag(trace: list) -> Optional[str]:
    """Detect if the agent found flag-like content but didn't claim it."""
    recent = trace[-3:]
    for s in recent:
        # Check both tool_output and thought for flag patterns
        output = str(s.get("tool_output") or "")
        thought = str(s.get("thought") or "")
        combined = (output + " " + thought).lower()
        if "flag{" in combined:
            tool_name = str(s.get("tool_name") or "")
            if "claim_flag" not in tool_name:
                return (
                    "You found a FLAG in the output but didn't claim it! "
                    "Use claim_flag IMMEDIATELY with the exact flag string. "
                    "Then record_finding and write_note about it."
                )
    return None


def _detect_phase_stall(trace: list, threshold: int = 20) -> Optional[str]:
    """Detect if agent is stuck in recon too long without exploiting."""
    if len(trace) < threshold:
        return None
    recent = trace[-threshold:]
    recon_tools = {
        "nmap",
        "gobuster",
        "ffuf",
        "feroxbuster",
        "amass",
        "whatweb",
        "subfinder",
        "httpx",
        "nikto",
        "sslscan",
        "shodan",
        "crtsh",
        "google_dork",
        "read_file",
        "web_search",
        "curl",
    }
    exploit_tools = {
        "http_request",
        "sqlmap_scan",
        "hydra",
        "execute_terminal",
        "deploy_subagent",
        "mcp_browser_goto",
        "msf_run",
    }
    recon_count = sum(1 for s in recent if s.get("tool_name", "") in recon_tools)
    exploit_count = sum(1 for s in recent if s.get("tool_name", "") in exploit_tools)
    if recon_count > 15 and exploit_count < 3:
        return (
            "FORCE EXPLOITATION: 15+ recon turns, <3 exploit attempts. "
            "You have enough data. TEST vulnerabilities NOW with http_request, "
            "mcp_browser_goto, or deploy_subagent with exploit tasks."
        )
    return None


def _detect_subagent_addiction(trace: list, threshold: int = 5) -> Optional[str]:
    """Detect when agent spawns subagents instead of working directly."""
    if len(trace) < threshold:
        return None
    recent_actions = [s.get("tool_name", "") for s in trace[-threshold:]]
    spawns = sum(1 for t in recent_actions if t == "deploy_subagent")
    direct = sum(
        1
        for t in recent_actions
        if t not in ("deploy_subagent", "write_note", "job_list", "job_status", "job_output", "check_knowledge")
    )
    if spawns >= 3 and direct < 2:
        return f"You spawned {spawns} subagents in {threshold} turns but did almost nothing yourself. Execute tools DIRECTLY."
    return None


def _detect_unverified_claim(trace: list) -> Optional[str]:
    """Detect when agent claims a finding without diff verification."""
    if len(trace) < 3:
        return None
    kw = ["SSTI confirmed", "SQLi found", "XSS detected", "RCE achieved", "vulnerability confirmed"]
    for i in range(len(trace) - 2, len(trace)):
        thought = str(trace[i].get("thought", "")).lower()
        if any(k.lower() in thought for k in kw):
            tools_since = [s.get("tool_name", "") for s in trace[i:]]
            if "diff_responses" not in tools_since and "diff_engine" not in tools_since:
                return "You claimed a finding without verifying with diff_responses. Verify your claims NOW."
    return None


# ── Main supervisor ───────────────────────────────────────────────────


def analyze_trace(trace: list, **extra_kw) -> Optional[str]:
    """Analyze recent execution trace and return guidance if intervention needed.

    Args:
        trace: List of execution step dicts (last 10-15 entries).
        extra_kw: Additional state data for context-aware detectors.

    Returns:
        Guidance string if intervention needed, None otherwise.
    """
    if not trace:
        return None

    detectors = [
        _detect_missed_flag,
        _detect_phase_stall,
        _detect_repeating_tool,
        _detect_bookkeeping_loop,
        _detect_found_but_not_exploited,
        _detect_subagent_addiction,
        _detect_subagents_failing,
        _detect_unverified_claim,
        _detect_no_progress,
        _detect_dead_end,
        _detect_payload_class_escalation,
    ]

    for detector in detectors:
        guidance = detector(trace, **extra_kw)
        if guidance:
            logger.info(f"Supervisor intervention: {detector.__name__}")
            return guidance

    return None


# ── LLM-Powered Deep Analysis ────────────────────────────────────────────────

_llm_supervisor_prompt = """You are a supervisor monitoring an autonomous security agent.
Review the last 10 execution steps and the current state. Identify:

1. MISSED OPPORTUNITIES: Did the agent find something but fail to exploit it?
2. INEFFICIENT PATTERNS: Is the agent stuck in a loop or wasting iterations?
3. STRATEGIC ERRORS: Is the agent using the wrong technique for the target?
4. PHASE MISMATCH: Is the agent doing recon when it should be exploiting?

Current phase: {phase}
Objective: {objective}
Total iterations so far: {iterations}
Cost so far: ${cost:.4f}

Recent execution trace:
{trace_summary}

Respond with EXACTLY ONE of:
- "NO_ISSUES" if everything looks correct
- A single concise guidance sentence (max 150 chars) if you see a problem

Your guidance will be injected as a supervisor message into the agent's next turn.
Be specific. Reference exact tool names, ports, or endpoints."""


async def analyze_trace_with_llm(
    trace: list,
    state: dict,
    generate_fn,
) -> str | None:
    """Use an LLM to perform deeper analysis of the agent's behavior.

    This runs AFTER pattern-based detection finds nothing. It catches
    subtle issues that regex patterns miss: strategic errors, missed
    chaining opportunities, inefficient tool selection.
    """
    if not trace or not generate_fn:
        return None

    lines = []
    for i, step in enumerate(trace[-10:], 1):
        tn = step.get("tool_name", "?")
        thought = str(step.get("thought", ""))[:120]
        success = "OK" if step.get("success", True) else "FAIL"
        lines.append(f"  {i}. [{tn}] ({success}) {thought}")

    trace_summary = "\n".join(lines)
    if len(trace_summary) > 2000:
        trace_summary = trace_summary[:2000] + "\n  ... (truncated)"

    prompt = _llm_supervisor_prompt.format(
        phase=state.get("current_phase", "informational"),
        objective=str(state.get("original_objective", ""))[:200],
        iterations=state.get("current_iteration", 0),
        cost=state.get("total_cost_usd", 0.0),
        trace_summary=trace_summary,
    )

    try:
        response = await generate_fn(
            model_id=None,
            prompt=prompt,
            system="You are a concise security supervisor. Respond with one line.",
            max_tokens=150,
            temperature=0.1,
        )
    except Exception:
        return None

    if not response or "NO_ISSUES" in response.upper():
        return None

    guidance = response.strip().strip('"').strip("'")
    if len(guidance) > 250:
        guidance = guidance[:250] + "..."
    return guidance


# ── Wave 2 detectors (A6 dead-end, A9 payload-class escalation) ────────


def _detect_dead_end(trace: list, fail_threshold: int = 3) -> Optional[str]:
    """A6: same tool FAILING repeatedly with varying args — a dead end.
    Distinct from _detect_repeating_tool (same args, any outcome): here
    the agent IS varying its approach inside one tool and still losing.
    The fix is not another variant — it's a different strategy CLASS.
    """
    if len(trace) < fail_threshold:
        return None
    recent = trace[-fail_threshold:]
    tools = [s.get("tool_name", "") for s in recent]
    if len(set(tools)) != 1 or not tools[0]:
        return None
    if all(not s.get("success", True) for s in recent):
        return (
            f"DEAD END: '{tools[0]}' failed {fail_threshold} times in a row with different inputs. "
            "Do not call it again with minor variations. Switch strategy CLASS entirely: "
            "different attack surface, different recon angle, or use deploy_subagent for a fresh pass. "
            "Write a note about why this path is blocked, then move on."
        )
    return None


_INJECTION_TOOLS = {"http_request", "execute_terminal", "custom_cmd_run"}
_PAYLOAD_FAMILIES = {
    "reflected": ("' OR 1=1", "<script>", "onerror=", "../../"),
    "blind": ("SLEEP(", "WAITFOR", "BENCHMARK(", "pg_sleep"),
    "timing": ("sleep", "waitfor", "delay"),
}


def _detect_payload_class_escalation(trace: list, fail_threshold: int = 4) -> Optional[str]:
    """A9: injection-style failures repeating — escalate the PAYLOAD CLASS
    (reflected -> blind -> timing/oob), not just the payload string."""
    if len(trace) < fail_threshold:
        return None
    recent = trace[-fail_threshold:]
    inj = [s for s in recent if s.get("tool_name") in _INJECTION_TOOLS]
    if len(inj) < fail_threshold or any(s.get("success") for s in inj):
        return None
    fam = set()
    for s in inj:
        args = str(s.get("tool_args", ""))
        for name, markers in _PAYLOAD_FAMILIES.items():
            if any(m.lower() in args.lower() for m in markers):
                fam.add(name)
    hint = ""
    if fam and fam <= {"reflected"}:
        hint = " You keep testing REFLECTED variants — escalate to BLIND (boolean/time-based) or OUT-OF-BAND (DNS/callback canaries via ssrf_canary)."
    elif fam and "reflected" in fam and "blind" in fam:
        hint = " You've tried reflected and blind — move fully to TIMING-based or OOB confirmation, or accept the surface is hardened."
    return (
        f"PAYLOAD CLASS ESCALATION: {fail_threshold} injection attempts failed. "
        "Varying the payload string is not working." + hint
    ) or None


def _confidence_from_decision(decision: dict) -> str:
    """A8 helper: normalize a decision's confidence claim."""
    raw = str(decision.get("confidence", "")).lower()
    if raw in ("verified", "confirmed", "certain"):
        return "verified"
    if raw in ("probable", "likely", "high"):
        return "probable"
    if raw in ("suspected", "possible", "low", "maybe"):
        return "suspected"
    return "probable"  # default when unclaimed — findings are never 'verified' without proof
