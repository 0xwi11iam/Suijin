"""Generate response node — produces the final report or conversational response.

Ported and simplified from redamon/agentic/orchestrator_helpers/nodes/generate_response_node.py.
"""

import logging

from suijin.core.state import (
    format_execution_trace,
    format_todo_list,
)
from suijin.helpers.json_utils import json_dumps_safe

logger = logging.getLogger(__name__)

FINAL_REPORT_PROMPT = """# ROLE: Penetration Testing Report Writer

You are a professional security consultant. Compile a structured penetration
test report from the execution trace below.

## OBJECTIVE
{objective}

## ENGAGEMENT SUMMARY
- Total iterations: {iteration_count}
- Final phase: {final_phase}
- Completion reason: {completion_reason}

## TARGET INTELLIGENCE
{target_info}

## EXECUTION TRACE
{execution_trace}

## TASK LIST
{todo_list}

## INSTRUCTIONS
Write a concise penetration test report with these sections:
1. **Executive Summary** — one paragraph on what was found
2. **Attack Surface** — what was discovered during recon
3. **Vulnerabilities Found** — list each with severity (Critical/High/Medium/Low)
4. **Exploitation Summary** — what was successfully exploited
5. **Recommendations** — actionable fixes

Use the execution trace as your sole source of truth. Do not hallucinate findings.
"""


async def generate_response_node(state: dict, *, generate_fn) -> dict:
    """Generate the final response/report.

    Args:
        state: Current agent state dict.
        generate_fn: Async callable (messages, config) -> str (LLM response).
    """
    if state.get("_abort_transition"):
        return {"_abort_transition": False}

    if state.get("_guardrail_blocked"):
        return {}

    objective = state.get("original_objective", "")
    iteration_count = state.get("current_iteration", 0)
    final_phase = state.get("current_phase", "informational")
    completion_reason = state.get("completion_reason", "Session ended")

    exec_trace = format_execution_trace(state.get("execution_trace", []))
    target_info = json_dumps_safe(state.get("target_info", {}), indent=2)
    todos = format_todo_list(state.get("todo_list", []))

    prompt = FINAL_REPORT_PROMPT.format(
        objective=objective,
        iteration_count=iteration_count,
        final_phase=final_phase,
        completion_reason=completion_reason,
        target_info=target_info,
        execution_trace=exec_trace,
        todo_list=todos,
    )

    messages = [{"role": "user", "content": prompt}]
    config = {}  # Use defaults

    try:
        response = await generate_fn(messages, config)
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        response = f"Report generation failed: {e}"

    return {
        "messages": [{"role": "assistant", "content": response}],
        "completion_reason": completion_reason,
    }
