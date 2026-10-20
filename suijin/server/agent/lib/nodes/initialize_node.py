"""Initialize node — sets up a new agent session.

Prepares the initial AgentState with objective, phase, target info,
and system prompt. Called once at the start of a graph invocation.
"""

import logging

from suijin.modules.agent.lib.state import (
    ConversationObjective,
    PhaseHistoryEntry,
    TargetInfo,
    utc_now,
)

logger = logging.getLogger(__name__)


def initialize_node(state: dict, *, objective: str, config: dict = None) -> dict:
    """Initialize agent state for a new engagement.

    Args:
        state: Raw state dict (may be empty or from checkpoint).
        objective: The user's objective string.
        config: Optional config dict with max_iterations, etc.
    """
    max_iters = (config or {}).get("max_iterations", 100000)
    # (2026-10-20: the 100 default was the phantom-limit's last hiding
    # spot — the product default is INFINITE; only an explicit config
    # value ever limits a run)

    # If resuming from checkpoint, don't reinitialize — BUT always
    # carry max_iterations through (2026-10-19: every .sje resume ran
    # with a phantom 100-iteration limit because this key wasn't in
    # REPLAY_KEYS and the early return dropped it)
    if state.get("original_objective") and state.get("current_iteration", 0) > 0:
        logger.info("Resuming from checkpoint — skipping initialization")
        result = {}
        if max_iters and int(state.get("max_iterations") or 0) != int(max_iters):
            result["max_iterations"] = max_iters
            result["_run_config"] = dict(state.get("_run_config") or {})
            result["_run_config"]["max_iterations"] = max_iters
        return result

    now = utc_now()

    # Build initial objective
    objectives = [
        ConversationObjective(
            content=objective,
            created_at=now,
        ).model_dump()
    ]

    # Initial phase history
    phase_history = [PhaseHistoryEntry(phase="informational", entered_at=now).model_dump()]

    # Empty target info
    target_info = TargetInfo().model_dump()

    # System message
    messages = [
        {
            "role": "system",
            "content": "",  # Filled by think_node each turn
        },
        {
            "role": "user",
            "content": f"OBJECTIVE: {objective}",
        },
    ]

    return {
        "original_objective": objective,
        "conversation_objectives": objectives,
        "current_objective_index": 0,
        "objective_history": [],
        "current_phase": "informational",
        "phase_history": phase_history,
        "current_iteration": 0,
        "max_iterations": max_iters,
        "execution_trace": [],
        "todo_list": [],
        "target_info": target_info,
        "chain_findings_memory": [],
        "chain_failures_memory": [],
        "tested_axes": {},
        "messages": messages,
        "pending_questions": [],
    }
