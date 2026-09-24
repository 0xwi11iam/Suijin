"""
suijin/drift_analyser.py — Goal-drift detection (thin compatibility wrapper).

Supervisor.py imports this module. Since the old keyword-based drift_analyser
was deprecated in favor of agent_helpers/productivity.py, this file provides
the `analyse_drift()` function supervisor.py expects, using the new
productivity heuristics under the hood.
"""

from datetime import datetime


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

    for i, action in enumerate(actions):
        action_lower = action.lower()
        action_keywords = set(action_lower.split())
        overlap = len(goal_keywords & action_keywords) / max(len(goal_keywords), 1)

        patterns_hit = []

        # Detect goal mismatch
        if overlap < 0.1 and i > 2:
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
