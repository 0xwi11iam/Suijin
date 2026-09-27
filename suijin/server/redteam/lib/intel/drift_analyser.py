"""
suijin/drift_analyser.py — Goal-drift detection (thin compatibility wrapper).

Supervisor.py imports this module. Since the old keyword-based drift_analyser
was deprecated in favor of agent_helpers/productivity.py, this file provides
the `analyse_drift()` function supervisor.py expects, using the new
productivity heuristics under the hood.
"""

from datetime import datetime

#: readable labels for the pattern vocabulary (raw snake_case read as noise)
_PATTERN_LABELS = {
    "low_goal_overlap": "low goal overlap",
    "hallucination": "uncertainty language",
    "exfiltration": "exfiltration language",
}


def format_cause(cause: dict) -> str:
    """One drift cause as a line a human reads: `#3 http_request — low goal
    overlap: Inspect the live robots policy…`.

    The old rendering str()'d the whole dict — truncated mid-word action
    text, snake_case patterns, quotes and braces — which read as garbage
    both in the TUI note and in the model-facing DRIFT WARNING message.
    """
    action = str(cause.get("action", "")).strip() if isinstance(cause, dict) else str(cause)
    if not isinstance(cause, dict):
        # legacy/plain causes are bare description strings — render them
        # as-is (never crash on shape: that is the bug class this replaces)
        return action[:72] + ("…" if len(action) > 72 else "")
    tool, sep, rest = action.partition(":")
    desc = rest.strip() if sep and rest.strip() else action
    if len(desc) > 72:  # word-boundary truncation, never mid-word
        desc = desc[:72].rsplit(" ", 1)[0] + "…"
    patterns = ", ".join(_PATTERN_LABELS.get(p, p) for p in cause.get("patterns", []))
    idx = cause.get("action_index", "?")
    prefix = tool.strip() if sep and tool.strip() else "action"
    return f"#{idx} {prefix} — {patterns}: {desc}" if patterns else f"#{idx} {prefix}: {desc}"


def analyse_drift(original_goal: str, actions: list) -> dict:
    """Analyse whether the agent's actions have drifted from the goal.

    Args:
        original_goal: The original objective string.
        actions: List of action description strings (tool: arg format).

    Returns:
        Dict with drift_detected, drift_count, drift_causes, suggestions.
        Matches the contract supervisor.py expects.
    """
    if not actions:
        return {
            "timestamp": datetime.now().isoformat(),
            "original_goal": original_goal,
            "total_actions": 0,
            "drift_detected": False,
            "drift_count": 0,
            "drift_causes": [],
            "suggestions": ["No actions recorded yet."],
        }

    goal_keywords = set(original_goal.lower().split())
    drift_causes = []

    # Intel/memory consultation is doctrine-aligned, not drift: consulting
    # the dossier, KG or notes naturally shares few keywords with the
    # objective prose (a bare hostname overlaps nothing), so low_goal_overlap
    # fired on EXACTLY the mandated behaviour (field run: target_dossier
    # and robots-policy http_request flagged as drift while the agent was
    # doing pre-test diligence correctly).
    _INTEL_TOOLS = (
        "target_dossier",
        "check_knowledge",
        "memory_recall",
        "search_kb",
        "search_cve",
        "write_note",
        "read_file",
    )

    for i, action in enumerate(actions):
        action_lower = action.lower()
        action_keywords = set(action_lower.split())
        overlap = len(goal_keywords & action_keywords) / max(len(goal_keywords), 1)
        _tool = action_lower.split(":", 1)[0].strip()
        _is_intel = _tool in _INTEL_TOOLS

        patterns_hit = []

        # Detect goal mismatch
        if overlap < 0.1 and i > 2 and not _is_intel:
            patterns_hit.append("low_goal_overlap")

        # Detect hallucination indicators
        for kw in ("i think", "probably", "maybe", "not sure", "assuming"):
            if kw in action_lower:
                patterns_hit.append("hallucination")
                break

        # Detect exfiltration indicators
        for kw in ("exfiltrate", "send data", "upload", "leak"):
            if kw in action_lower:
                patterns_hit.append("exfiltration")
                break

        if patterns_hit:
            drift_causes.append(
                {
                    "action_index": i,
                    "action": action[:100],
                    "patterns": patterns_hit,
                }
            )

    drift_detected = len(drift_causes) > 0

    suggestions = []
    if drift_detected:
        all_patterns = {p for c in drift_causes for p in c["patterns"]}
        if "hallucination" in all_patterns:
            suggestions.append("Agent showed uncertainty — add verification steps.")
        if "exfiltration" in all_patterns:
            suggestions.append("Agent attempted data exfiltration — review scope.")
        if "low_goal_overlap" in all_patterns:
            suggestions.append("Agent actions diverged from goal — reinforce objective.")

    if not suggestions:
        suggestions.append("No significant drift detected. Agent stayed on target.")

    return {
        "timestamp": datetime.now().isoformat(),
        "original_goal": original_goal,
        "total_actions": len(actions),
        "drift_detected": drift_detected,
        "drift_count": len(drift_causes),
        "drift_causes": drift_causes,
        "suggestions": suggestions,
    }
