"""
Subagent spawning system — lets the main agent deploy parallel subagents.

Each subagent runs in its own asyncio task with a focused objective,
independent LLM calls, and returns results to the main agent.

Key design decisions:
- 15s LLM timeout, 30s tool timeout, 3 max steps — keeps subagents fast
- Partial results returned even on timeout (nothing lost)
- Auto-completes after 2 consecutive failures to avoid wasted cycles
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Tunable constants ────────────────────────────────────────────────────────
LLM_TIMEOUT = 15       # seconds per subagent LLM call
TOOL_TIMEOUT = 30       # seconds per subagent tool execution
MAX_SUBAGENT_STEPS = 3  # steps before auto-complete
BATCH_TIMEOUT = 60      # seconds for ALL subagents combined


class SubagentResult:
    """Result from a completed subagent."""
    def __init__(self, subagent_id: str, task: str, success: bool,
                 findings: str, steps: int, partial: bool = False):
        self.subagent_id = subagent_id
        self.task = task
        self.success = success
        self.findings = findings
        self.steps = steps
        self.partial = partial
        self.completed_at = datetime.now(timezone.utc).isoformat()


async def run_subagent(
    task: str,
    *,
    generate_fn,
    route_tool_fn,
    tool_catalog_fn,
    max_steps: int = MAX_SUBAGENT_STEPS,
) -> SubagentResult:
    """Run a focused subagent on a specific task.

    The subagent runs a tight think→execute→think loop with short timeouts.
    Results are returned to the main agent for incorporation into the main loop.
    """
    subagent_id = uuid.uuid4().hex[:8]
    logger.info(f"Subagent [{subagent_id}] starting: {task[:100]}")

    system_prompt = f"""# ROLE: Focused Subagent — ONE task only.

## TASK
{task}

## RULES
1. Focus ONLY on the task above. No recon, no enumeration, no scope creep.
2. You have {max_steps} steps MAXIMUM. Be direct. Use execute_terminal for commands.
3. Report findings CLEARLY — what worked, what didn't, what you discovered.
4. When done OR stuck, respond with action="complete" immediately.
5. If a tool fails twice in a row, STOP and complete — don't retry.

## TOOL REFERENCE (use EXACTLY these formats):
- execute_terminal: {{"cmd": "curl -s http://target/path"}}
- http_request: {{"method": "GET", "url": "http://target/path"}}
- write_note: {{"content": "finding details", "success": true, "category": "finding"}}
- search_cve: {{"service": "apache", "version": "2.4.49"}}
- read_file: {{"file_path": "/tmp/output.txt"}}
- write_file: {{"file_path": "/tmp/payload.txt", "content": "payload here"}}
- check_knowledge: {{"target": "127.0.0.1"}}
- record_finding: {{"target": "127.0.0.1", "finding_type": "verified_cve", "rule": "details", "evidence": "proof"}}

## OUTPUT FORMAT — Exactly ONE JSON per turn:
{{"action": "use_tool", "thought": "testing SQLi on /login", "tool_name": "execute_terminal", "tool_args": {{"cmd": "curl -s 'http://target/login?user=admin'\" OR '1'='1\"'"}}}}
{{"action": "complete", "thought": "done", "completion_reason": "SQLi confirmed on /login via time-based blind"}}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Execute this task NOW: {task}"},
    ]

    findings_parts = []
    success = False
    consecutive_failures = 0
    step_num = 0

    for step_num in range(1, max_steps + 1):
        # ── Circuit breaker: 2 consecutive failures → stop ──────────
        if consecutive_failures >= 2:
            findings_parts.append("Auto-stopped after 2 consecutive failures.")
            break

        try:
            # ── LLM call with short timeout ─────────────────────────
            try:
                response = await asyncio.wait_for(
                    generate_fn(messages, {}),
                    timeout=LLM_TIMEOUT,
                )
            except asyncio.TimeoutError:
                findings_parts.append(f"[step {step_num}] LLM timed out ({LLM_TIMEOUT}s)")
                consecutive_failures += 1
                continue
            except Exception as e:
                findings_parts.append(f"[step {step_num}] LLM error: {e}")
                consecutive_failures += 1
                continue

            messages.append({"role": "assistant", "content": str(response)})
            consecutive_failures = 0  # reset — got a response

            # ── Parse JSON from response ────────────────────────────
            json_match = re.search(r'\{[^{}]*"action"\s*:\s*"[^"]+"[^{}]*\}', str(response), re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{[\s\S]*\}', str(response))

            if not json_match:
                findings_parts.append(f"[step {step_num}] No JSON in response: {str(response)[:150]}")
                messages.append({"role": "user", "content": 'No JSON found. Output ONLY: {"action": "use_tool", "thought": "...", "tool_name": "...", "tool_args": {...}}'})
                consecutive_failures += 1
                continue

            try:
                decision = json.loads(json_match.group(0))
            except json.JSONDecodeError as e:
                findings_parts.append(f"[step {step_num}] Bad JSON: {str(e)[:80]}")
                messages.append({"role": "user", "content": "Invalid JSON. Fix and retry with valid JSON only."})
                consecutive_failures += 1
                continue

            action = decision.get("action", "")

            # ── Handle complete ─────────────────────────────────────
            if action == "complete":
                reason = decision.get("completion_reason", decision.get("thought", "Task done"))
                findings_parts.append(f"[COMPLETE] {reason}")
                success = True
                break

            # ── Handle tool use ─────────────────────────────────────
            if action == "use_tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args") or {}

                if not tool_name:
                    messages.append({"role": "user", "content": "Missing tool_name. Must specify a tool from the reference list."})
                    continue

                try:
                    # Run tool in thread with timeout
                    result = await asyncio.wait_for(
                        asyncio.to_thread(route_tool_fn, tool_name, tool_args, {}),
                        timeout=TOOL_TIMEOUT,
                    )
                    result_str = str(result)[:800]
                    findings_parts.append(f"[{tool_name}] {result_str}")
                    messages.append({"role": "user", "content": f"Tool output:\n{result_str}"})
                    consecutive_failures = 0
                except asyncio.TimeoutError:
                    findings_parts.append(f"[{tool_name}] timed out ({TOOL_TIMEOUT}s)")
                    messages.append({"role": "user", "content": f"Tool {tool_name} timed out. Try a simpler command or complete."})
                    consecutive_failures += 1
                except Exception as e:
                    messages.append({"role": "user", "content": f"Tool {tool_name} error: {e}. Try a different approach."})
                    consecutive_failures += 1
            else:
                messages.append({"role": "user", "content": f"Unknown action '{action}'. Use 'use_tool' or 'complete'."})

        except Exception as e:
            logger.warning(f"Subagent [{subagent_id}] step {step_num} crashed: {e}")
            findings_parts.append(f"[step {step_num}] Crash: {e}")
            break

    findings = "\n".join(findings_parts) if findings_parts else "(no output produced)"
    logger.info(f"Subagent [{subagent_id}] done: success={success}, steps={step_num}, findings_len={len(findings)}")

    return SubagentResult(
        subagent_id=subagent_id, task=task, success=success,
        findings=findings, steps=step_num,
    )


async def spawn_and_collect(
    tasks: list[str],
    generate_fn,
    route_tool_fn,
    tool_catalog_fn,
    max_concurrent: int = 3,
    total_timeout: float = BATCH_TIMEOUT,
) -> list[SubagentResult]:
    """Spawn multiple subagents in parallel, collect results with timeout.

    On timeout, returns partial results from completed subagents rather than
    discarding everything. Failed/crashed subagents return error results.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    completed: list[SubagentResult] = []

    async def _run_one(task: str) -> SubagentResult:
        try:
            async with semaphore:
                return await run_subagent(
                    task,
                    generate_fn=generate_fn,
                    route_tool_fn=route_tool_fn,
                    tool_catalog_fn=tool_catalog_fn,
                )
        except Exception as e:
            logger.error(f"Subagent crash: {task[:80]} — {e}")
            return SubagentResult(
                subagent_id="crash", task=task, success=False,
                findings=f"Subagent crashed: {e}", steps=0,
            )

    try:
        coros = [_run_one(t) for t in tasks]
        results = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=total_timeout,
        )
        # Unwrap exceptions
        for r in results:
            if isinstance(r, Exception):
                completed.append(SubagentResult(
                    subagent_id="error", task="(unknown)",
                    success=False, findings=f"Exception: {r}", steps=0,
                ))
            else:
                completed.append(r)
        return completed

    except asyncio.TimeoutError:
        logger.warning(f"Subagent batch timed out after {total_timeout}s")
        # Return whatever we have + timeout markers for the rest
        remaining = len(tasks) - len(completed)
        for i in range(remaining):
            idx = len(completed) + i
            task_text = tasks[idx] if idx < len(tasks) else "(unknown)"
            completed.append(SubagentResult(
                subagent_id="timeout", task=task_text, success=False,
                findings=f"Batch timed out ({total_timeout}s). Subagent did not complete.",
                steps=0, partial=True,
            ))
        return completed
