"""Think node — core ReAct reasoning with structured LLM output parsing.

The heart of the agent loop. Builds a comprehensive system prompt from
the current state, calls the LLM, parses the structured decision, and
updates state for the next graph transition.

Adapted from redamon/agentic/orchestrator_helpers/nodes/think_node.py.
"""

import asyncio
import logging
from uuid import uuid4

from suijin.modules.agent.lib.state import (
    ExecutionStep,
    PhaseHistoryEntry,
    format_chain_context,
    format_qa_history,
    format_todo_list,
)

logger = logging.getLogger(__name__)


def _tenant_ctx(user_id, project_id, session_id):
    from suijin.modules.platform.lib.agent_context import set_tenant_context

    return set_tenant_context(user_id, project_id, session_id)


def _phase_ctx(phase):
    from suijin.modules.platform.lib.agent_context import set_phase_context

    return set_phase_context(phase)


def _json_dumps_safe(*a, **k):
    from suijin.modules.platform.lib.helpers.json_utils import json_dumps_safe

    return json_dumps_safe(*a, **k)


def _try_parse_llm_decision(*a, **k):
    from suijin.modules.platform.lib.helpers.parsing import try_parse_llm_decision

    return try_parse_llm_decision(*a, **k)


def _productivity(name):
    from suijin.modules.platform.lib.helpers import productivity

    return getattr(productivity, name)


def _queued_plan_block(state: dict) -> str:
    """H4: plan_tools steps 2..N were dropped after step 1 executed — the
    remaining queue sat in state, invisible. Now every turn renders it
    until the plan finishes or the agent changes course."""
    remaining = state.get("_plan_remaining") or []
    if not remaining:
        return ""
    lines = ["## QUEUED PLAN (from your plan_tools — still pending)"]
    for i, s in enumerate(remaining[:6], 1):
        if isinstance(s, dict):
            tn = s.get("tool_name", "?")
            args = s.get("tool_args") or {}
            preview = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:3])
            lines.append(f"{i}. {tn} ({preview})" if preview else f"{i}. {tn}")
    lines.append("Emit these via use_tool (or switch_skill/plan_tools to change course) — the queue clears as you go.")
    return "\n".join(lines) + "\n"


def _render_board(state: dict) -> str:
    """H1: the engagement board — accumulated target intel, tested-axes
    coverage, and running background jobs (previously: a raw JSON dump of a
    skeleton that was never populated)."""
    try:
        from suijin.modules.agent.lib.target_board import render_board

        jobs = []
        try:
            from suijin.modules.tools.lib import job_registry

            jobs = [j.get("job_id") for j in job_registry.list_jobs() if j.get("status") == "running"]
        except Exception:  # noqa: BLE001 — job visibility must never break thinking
            pass
        return render_board(state.get("target_info") or {}, state.get("tested_axes") or {}, jobs)
    except Exception:  # noqa: BLE001 — board fallback is the old dump
        return _json_dumps_safe(state.get("target_info", {}), indent=2)


def _run_auto_actions(auto_actions: list, updates: dict, route_tool_fn=None):
    """Run lightweight side actions (write_note, check_knowledge, job_list, etc.)
    in the same iteration as the main tool. Results injected into messages.

    Auto-actions are FREE — they don't consume an iteration. Use them for:
    - write_note: log findings immediately
    - check_knowledge: query KG before next turn
    - record_finding: persist to KG
    - job_list: check background jobs
    - deploy_subagent: spawn parallel work (fires async, result in future turn)
    """
    if route_tool_fn is None:
        from suijin.modules.tools.lib.dispatch import route_tool as route_tool
    else:
        route_tool = route_tool_fn

    for aa in auto_actions:
        if not isinstance(aa, dict):
            continue
        aa_action = aa.get("action", "")
        aa_args = aa.get("args") or {}

        try:
            if aa_action == "write_note":
                result = route_tool("write_note", aa_args, {})
                updates["messages"].append({"role": "user", "content": f"AUTO: {result}"})

            elif aa_action == "check_knowledge":
                result = route_tool("check_knowledge", aa_args, {})
                updates["messages"].append({"role": "user", "content": f"AUTO KG: {result}"})

            elif aa_action == "record_finding":
                result = route_tool("record_finding", aa_args, {})
                updates["messages"].append({"role": "user", "content": f"AUTO KG: {result}"})

            elif aa_action == "job_list":
                result = route_tool("job_list", aa_args, {})
                updates["messages"].append({"role": "user", "content": f"AUTO JOBS: {result}"})

            elif aa_action == "deploy_subagent":
                # Inject a message telling the agent to use the action format next turn
                task = aa_args.get("subagent_task", "")
                if task:
                    updates["messages"].append(
                        {
                            "role": "user",
                            "content": (
                                f"AUTO: Subagent task queued: {task[:200]}\n"
                                f'Use action="deploy_subagent" with subagent_task="{task[:150]}" to execute.'
                            ),
                        }
                    )

            elif aa_action == "add_todo":
                desc = aa_args.get("description", "")
                if desc:
                    current_todos = updates.get("todo_list", [])
                    current_todos.append(
                        {
                            "id": str(uuid4())[:8],
                            "description": desc,
                            "status": "pending",
                            "priority": aa_args.get("priority", "high"),
                        }
                    )
                    updates["todo_list"] = current_todos

        except Exception as e:
            logger.warning(f"Auto-action {aa_action} failed: {e}")


async def think_node(state: dict, *, generate_fn, config: dict = None, route_tool_fn=None) -> dict:
    """Core ReAct reasoning node.

    Args:
        state: Current agent state dict.
        generate_fn: Async callable (messages, config) -> str (LLM response).
        config: Agent config dict (supervisor_interval, etc.).
    """
    user_id = state.get("user_id", "local")
    project_id = state.get("project_id", "default")
    session_id = state.get("session_id", "")

    iteration = state.get("current_iteration", 0) + 1
    phase = state.get("current_phase", "informational")

    logger.info(f"THINK: iter {iteration}, phase {phase}")

    _tenant_ctx(user_id, project_id, session_id)
    _phase_ctx(phase)

    # Live guidance (file-based, atomic): the operator's typed prompt
    # rides at the TOP of the system prompt — above doctrine, above the
    # engagement order, above everything. Read + consume each turn.
    from suijin.modules.agent.lib.live_guidance import read_and_clear_guidance

    _live_guidance = read_and_clear_guidance()
    _guidance_block = ""
    if _live_guidance:
        _guidance_block = (
            "\n## OPERATOR GUIDANCE (live — the human just said this, act on it NOW)\n" + _live_guidance + "\n"
        )

    # Build system prompt using the new skill-based builder — with the
    # BF2 blue seam: state["_blue_mode"] swaps in the blue prompt builder
    # (doctrine, blue tools, blue skills) and the defensive task order
    if state.get("_blue_mode"):
        from suijin.modules.blueteam.lib.blue.agent import blue_system_prompt, defensive_order

        system_prompt = _guidance_block + blue_system_prompt(state)
        user_turn = defensive_order(state.get("original_objective", ""))
    else:
        from suijin.modules.agent.lib.prompts.base import build_agent_system_prompt, engagement_order

        system_prompt = _guidance_block + build_agent_system_prompt(state)
        user_turn = engagement_order(state.get("original_objective", ""))

    # Add state context (chain, todos, QA) after the skill+tools prompt
    chain_context = format_chain_context(
        state.get("chain_findings_memory", []),
        state.get("chain_failures_memory", []),
        state.get("chain_decisions_memory", []),
        state.get("execution_trace", []),
        state.get("chain_waves_memory", []),
    )
    todo_context = format_todo_list(state.get("todo_list", []))
    qa_context = format_qa_history(state.get("qa_history", []))

    # Feed the agent its own recent output so it sees all tool results
    # Context compaction (A7): compress old history BEFORE it is embedded
    # into the prompt (recent message slices + summaries below read the
    # compacted list). Under budget this is a no-op.
    try:
        from suijin.modules.agent.lib.compact import compact as _compact_messages

        _msgs = state.get("messages") or []
        _compacted = _compact_messages(_msgs)
        if _compacted is not _msgs:
            state["messages"] = _compacted
    except Exception:  # noqa: BLE001 — compaction must never break thinking
        pass

    raw_msgs = state.get("messages", [])
    recent_msgs = ""
    # Token-budgeted embed: the newest messages verbatim, older ones
    # truncated — the compaction digest covers the deep past. Unbounded
    # embeds (15 x 5,000 chars) drowned the agent's attention.
    embed_budget = 24_000  # chars (~6k tokens) for the recent window
    for m in reversed(raw_msgs[-15:]):
        role = m.get("role", "?")
        content = str(m.get("content", ""))
        if len(recent_msgs) + len(content) + 12 > embed_budget:
            room = max(0, embed_budget - len(recent_msgs) - 40)
            if room > 200:
                recent_msgs = f"[{role}]: {content[:room]}...(truncated)\n" + recent_msgs
            break
        recent_msgs = f"[{role}]: {content}\n" + recent_msgs

    # Build a summary of the last 8 tool actions (anti-repeat)
    trace = state.get("execution_trace", [])
    action_log = ""
    for t in trace[-8:]:
        tn = t.get("tool_name", "")
        ta = str(t.get("tool_args", {}))[:120]
        succ = "OK" if t.get("success", True) else "FAIL"
        thought = t.get("thought", "")[:150]
        action_log += f"  [{succ}] {tn} {ta}\n"
        if thought:
            action_log += f"    thought: {thought}\n"

    context_block = f"""
## CURRENT STATE
- **Phase**: {phase}
- **Iteration**: {iteration}/{state.get("max_iterations", 100)}
- **Attack Path**: {state.get("attack_path_type", "recon")}
{_queued_plan_block(state)}
## RECENT ACTIONS (last 8 tool calls — DO NOT REPEAT FAILURES)
{action_log or "(none)"}

## RECENT MESSAGES (last 15 system/tool messages)
{recent_msgs or "(none)"}

## TARGET INTELLIGENCE (your working board — accumulated, trust it)
{_render_board(state)}

## TODO LIST
{todo_context}

## CHAIN CONTEXT (recent findings, failures)
{chain_context or "(no chain context yet)"}

## Q&A HISTORY
{qa_context or "(none)"}

## [warn] RULES
- NEVER repeat a FAILED action. If a tool failed, try a DIFFERENT approach.
- NEVER install tools you already tried to install. Use what's available.
- NEVER check job_status/job_list twice in a row without acting on results.
- If nmap/job has no output after 90s, it's probably blocked. Move on.
- READ the output of completed jobs BEFORE spawning new ones.
"""
    full_prompt = system_prompt + context_block

    # Live context manifest — the operator sees exactly what the AI was
    # fed this turn (guidance verbatim at the top, overwriting each turn)
    from suijin.modules.agent.lib.live_guidance import write_context_manifest

    write_context_manifest(
        guidance=_live_guidance or "",
        phase=phase,
        iteration=iteration,
        attack_path=str(state.get("attack_path_type", "recon")),
        recent_actions=action_log,
        msg_count=len(raw_msgs),
        prompt_chars=len(full_prompt),
    )

    # A1: the objective turn is a CONTRACTED ENGAGEMENT order, not a bare
    # request — the operator's authorization words lifted verbatim. A bare
    # "attack X" as the last-read text is where refusals anchored.
    from suijin.modules.agent.lib.prompts.base import engagement_order

    # Build messages
    messages = [
        {"role": "system", "content": full_prompt},
        {
            "role": "user",
            "content": user_turn,
        },
    ]

    # Scratchpad (C2): first turn of an engagement re-orients the agent
    # with its own notes (external memory — survives compaction).
    if int(state.get("current_iteration", 0) or 0) <= 1:
        try:
            from suijin.modules.agent.lib.scratchpad import scratchpad_message

            _pad = scratchpad_message()
            if _pad:
                state.setdefault("messages", []).append({"role": "user", "content": _pad})
        except Exception:  # noqa: BLE001
            pass

    # Fireteam (v5.1): drain finished background specialists into the
    # conversation — their findings arrive as messages on this turn.
    try:
        from suijin.modules.agent.lib.nodes.subagent_node import collect_finished_teams

        for _msg in collect_finished_teams():
            state.setdefault("messages", []).append({"role": "user", "content": _msg})
    except Exception:  # noqa: BLE001 — collection must never break thinking
        pass

    # H2: finished background JOBS drain too (fireteam symmetry) — results
    # used to vanish unless the agent remembered job_list (field trace: the
    # leaked-key scan was never collected)
    try:
        from suijin.modules.tools.lib import job_registry as _jr

        for _msg in _jr.collect_finished_jobs():
            state.setdefault("messages", []).append({"role": "user", "content": _msg})
    except Exception:  # noqa: BLE001 — same rule
        pass

    # Prompt profile (D31): snapshot token breakdown before the call
    try:
        from suijin.modules.agent.lib.profiler import record as _record_profile

        _record_profile(state)
    except Exception:  # noqa: BLE001 — profiling must never break thinking
        pass

    # ── LLM Call with retry ──────────────────────────────────────────
    max_parse_retries = 3
    decision = None
    parse_error = None
    raw_response = ""

    for attempt in range(max_parse_retries):
        try:
            raw_response = await generate_fn(messages, config or {})
        except Exception as e:
            logger.error(f"LLM call failed (attempt {attempt + 1}): {e}")
            if attempt < max_parse_retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            return {
                "messages": [{"role": "assistant", "content": f"Error: LLM call failed: {e}"}],
                "completion_reason": "llm_error",
            }

        decision, parse_error = _try_parse_llm_decision(raw_response)
        if decision is not None:
            break

        # Provider error: no point retrying parse, provider already retried internally.
        # One retry at think level for transient network glitches, then bail.
        if isinstance(raw_response, str) and raw_response.startswith("Error:"):
            logger.warning(f"Provider error: {raw_response[:200]}")
            if attempt == 0:
                await asyncio.sleep(3)
                continue
            return {
                "messages": [{"role": "user", "content": f"SYSTEM: Provider error: {raw_response}"}],
                "current_iteration": iteration,
                "completion_reason": "provider_failure",
                "final_summary": f"Agent stopped: {raw_response}",
            }

        logger.debug(f"Parse attempt {attempt + 1} failed: {parse_error}")  # console rendering lives in the UI
        if attempt < max_parse_retries - 1:
            messages.append({"role": "assistant", "content": raw_response})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your response could not be parsed. Error: {parse_error}\n"
                        "Respond with EXACTLY ONE JSON object and NOTHING else:\n"
                        '{"action": "use_tool", "tool_name": "<tool>", "tool_args": {...}, "thought": "..."}\n'
                        "Pick a tool from the tool list."
                    ),
                }
            )

    if decision is None:
        logger.error(f"All parse attempts failed. Last error: {parse_error}")
        return {
            "messages": [
                {"role": "assistant", "content": raw_response},
                {
                    "role": "user",
                    "content": f"SYSTEM: JSON parse failed after {max_parse_retries} attempts: {parse_error}",
                },
            ],
            "current_iteration": iteration,
            "completion_reason": "parse_failure",
            "final_summary": f"Agent stopped: LLM output could not be parsed after {max_parse_retries} attempts.",
        }

    # ── Process decision ─────────────────────────────────────────────
    action = decision.get("action", "use_tool")
    thought = decision.get("thought", "")
    reasoning = decision.get("reasoning", "")

    updates: dict = {
        "current_iteration": iteration,
        "messages": [{"role": "assistant", "content": raw_response}],
    }

    # A8: confidence tagging — normalize the decision's claim; the report
    # writer and verifier consume it. Unclaimed findings are 'probable'.
    try:
        from suijin.modules.agent.lib.supervisor import _confidence_from_decision

        updates["_finding_confidence"] = _confidence_from_decision(decision)
    except Exception:  # noqa: BLE001 — tagging must never break thinking
        pass

    # ── Output analysis & productivity ──────────────────────────────
    output_analysis = decision.get("output_analysis") or {}
    productivity = output_analysis.get("productivity") or {}

    # ── Chain findings ──────────────────────────────────────────────
    chain_findings = output_analysis.get("chain_findings") or []
    prev_findings = state.get("chain_findings_memory", [])
    # ── Todo updates ────────────────────────────────────────────────
    todo_updates = decision.get("todo_updates") or []
    current_todos = state.get("todo_list", [])
    if todo_updates:
        todo_map = {t.get("id"): t for t in current_todos if isinstance(t, dict)}
        for update in todo_updates:
            if not isinstance(update, dict):
                continue
            uid = update.get("id") or str(uuid4())[:8]
            update["id"] = uid
            todo_map[uid] = update
        current_todos = list(todo_map.values())
        updates["todo_list"] = current_todos

    # ── Build execution step ────────────────────────────────────────
    step = ExecutionStep(
        iteration=iteration,
        phase=phase,
        thought=thought,
        reasoning=reasoning,
        tool_name=decision.get("tool_name"),
        tool_args=decision.get("tool_args") or {},
        output_analysis=_json_dumps_safe(output_analysis) if output_analysis else None,
        productivity=productivity if productivity else None,
    ).model_dump()

    exec_trace = state.get("execution_trace", []) + [step]

    # ── Productivity tracking ────────────────────────────────────────
    # H1: the honest growth signal — execute_tool_node merges board updates
    # and sets _target_grew_last_step; the old code compared a dict with
    # itself here (always False), ratcheting the stall counter forever
    state_grew = bool(state.get("_target_grew_last_step"))

    # ── Axis tracking (complete — not a stub) ────────────────────────
    tested_axes = dict(state.get("tested_axes", {}))
    axis = _productivity("extract_axis")(step)
    if axis:
        # Pre-record the axis; success flag updated post-execution
        # via _tool_result in execute_tool_node
        if axis not in tested_axes:
            tested_axes[axis] = {"attempts": 1, "failures": 0}
        else:
            tested_axes[axis]["attempts"] = tested_axes[axis].get("attempts", 0) + 1

    # ── Audit productivity claim ──────────────────────────────────────
    if productivity:
        discrepancy = _productivity("audit_productivity_claim")(
            productivity,
            {},
            [],
            False,
        )
        if discrepancy:
            # Downgrade: LLM lied about making progress
            productivity = _productivity("downgrade_verdict_to_no_progress")(productivity, discrepancy)
            step["productivity"] = productivity

    # ── Handle action types ──────────────────────────────────────────

    if action == "use_tool":
        tool_name = decision.get("tool_name")
        tool_args = decision.get("tool_args") or {}

        if not tool_name:
            # clear the stale step — the router must NOT re-execute the
            # previous turn's tool on a malformed use_tool decision
            updates["_current_step"] = {}
            updates["messages"].append(
                {
                    "role": "user",
                    "content": "SYSTEM: action=use_tool requires a tool_name. Please specify which tool to use.",
                }
            )
            updates["execution_trace"] = exec_trace
            updates["current_iteration"] = iteration
            return updates

        updates["_current_step"] = {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "iteration": iteration,
            "phase": phase,
            "thought": thought,
            "reasoning": reasoning,
            "confidence": updates.get("_finding_confidence", "probable"),
            "productivity": productivity,
        }

        # ── Auto-actions: free side commands that run alongside main tool ──
        auto_actions = decision.get("auto_actions") or []
        if auto_actions:
            _run_auto_actions(auto_actions, updates, route_tool_fn)

        # H4: drain the queued plan — if this use_tool matches the queued
        # head, pop it so the queue shrinks as the plan executes
        _remaining = list(state.get("_plan_remaining") or [])
        if _remaining and isinstance(_remaining[0], dict) and _remaining[0].get("tool_name") == tool_name:
            updates["_plan_remaining"] = _remaining[1:]

    elif action == "plan_tools":
        tool_plan = decision.get("tool_plan") or {}
        steps_list = tool_plan.get("steps", [])
        if not steps_list:
            updates["messages"].append(
                {
                    "role": "user",
                    "content": "SYSTEM: plan_tools requires at least one step in tool_plan.steps.",
                }
            )
        else:
            first = steps_list[0]
            updates["_current_step"] = {
                "tool_name": first.get("tool_name"),
                "tool_args": first.get("tool_args") or {},
                "iteration": iteration,
                "phase": phase,
                "thought": thought,
                "reasoning": reasoning,
                "productivity": productivity,
            }
            # H4: top-level, or execute_tool_node's _current_step overwrite
            # drops it (plans lost their steps 2..N exactly here)
            updates["_plan_remaining"] = steps_list[1:]

        # ── Auto-actions ──
        auto_actions = decision.get("auto_actions") or []
        if auto_actions:
            _run_auto_actions(auto_actions, updates, route_tool_fn)

    elif action == "transition_phase":
        pt = decision.get("phase_transition") or {}
        to_phase = pt.get("to_phase", phase)
        reason = pt.get("reason", "")

        # clear the stale step — a pure bookkeeping turn must not re-execute
        # the previous tool (router keys on _current_step.tool_name)
        updates["_current_step"] = {}

        # ── FREEDOM: no phase-transition gating. Agent decides when to move. ──

        if to_phase == phase:
            updates["messages"].append(
                {
                    "role": "user",
                    "content": f"SYSTEM: Already in {phase} phase. No transition needed.",
                }
            )
        else:
            updates["current_phase"] = to_phase
            updates["_just_transitioned_to"] = to_phase
            phase_history = state.get("phase_history", []) + [PhaseHistoryEntry(phase=to_phase).model_dump()]
            updates["phase_history"] = phase_history
            updates["messages"].append(
                {
                    "role": "user",
                    "content": f"PHASE TRANSITION: Now in {to_phase} phase. Reason: {reason}\n"
                    f"You may now use {to_phase}-appropriate tools. Proceed with your next action.",
                }
            )

    elif action == "ask_operator":
        question = decision.get("question", "Need operator guidance.")
        updates["_current_step"] = {
            "tool_name": "ask_operator",
            "tool_args": {"question": question},
            "iteration": iteration,
            "phase": phase,
            "thought": thought,
            "reasoning": reasoning,
        }

    elif action == "complete":
        completion_reason = decision.get("completion_reason", "Objective complete")
        updates["_current_step"] = {}  # no execute hop on completion
        updates["completion_reason"] = completion_reason
        updates["messages"].append(
            {
                "role": "user",
                "content": f"OBJECTIVE COMPLETE: {completion_reason}",
            }
        )

    elif action == "ask_user":
        uq = decision.get("user_question") or {}
        question = uq.get("question", "No question specified")
        pending = state.get("pending_questions", []) + [uq]
        updates["_current_step"] = {}  # pure question turn — no execute hop
        updates["pending_questions"] = pending
        updates["messages"].append(
            {
                "role": "user",
                "content": f"AGENT QUESTION: {question}\n(Answer will be collected from the operator.)",
            }
        )

    elif action == "deploy_subagent":
        # Fireteam (v5.1): NON-BLOCKING. The team runs in the background;
        # this turn returns instantly with a team id. Results arrive as
        # FIRETEAM RESULT messages on future turns (drained at think start);
        # fireteam_status() polls progress.
        subagent_task = decision.get("subagent_task", "")
        if not subagent_task:
            updates["messages"].append(
                {
                    "role": "user",
                    "content": "SYSTEM: deploy_subagent requires a subagent_task field (tasks separated by ||).",
                }
            )
        else:
            tasks = [t.strip() for t in subagent_task.split("||") if t.strip()][:5]
            updates["_current_step"] = {
                "tool_name": "",  # empty = router sends back to think (no execute hop)
                "tool_args": {"tasks": len(tasks), "preview": tasks[0][:100]},
                "iteration": iteration,
                "phase": phase,
                "thought": thought,
                "reasoning": reasoning,
                "tool_output": "",  # set AFTER deploy — never claim success before it happened
            }
            try:
                from suijin.modules.agent.lib.nodes.subagent_node import deploy_fireteam

                if route_tool_fn is not None:
                    _rt = route_tool_fn  # blue graph: responders route BLUE tools
                else:
                    from suijin.modules.tools.lib.dispatch import route_tool as _rt

                dep = deploy_fireteam(tasks, generate_fn=generate_fn, route_tool_fn=_rt)
                if dep.get("team_id"):
                    updates["_current_step"]["tool_output"] = (
                        f"Fireteam {dep['team_id']}: {len(dep['spawned'])} deployed, "
                        f"{len(dep.get('skipped', []))} skipped"
                    )
                    content = (
                        f"FIRETEAM {dep['team_id']} DEPLOYED — {len(dep['spawned'])} specialist(s) running in the background:\n"
                        + "\n".join(f"  - {t[:160]}" for t in dep["spawned"])
                        + "\nResults arrive automatically on your next turns. Keep working meanwhile."
                    )
                    for t, reason in dep.get("skipped", []):
                        content += f"\n  SKIPPED (not deployed): {t[:80]} — {reason}"
                else:
                    # every task rejected — the message teaches why
                    updates["_current_step"]["tool_output"] = (
                        f"Fireteam NOT deployed — {len(dep.get('skipped', []))} task(s) rejected"
                    )
                    content = "FIRETEAM NOT DEPLOYED — every task was rejected as wasted effort:\n"
                    content += "\n".join(f"  - {t[:80]} — {reason}" for t, reason in dep.get("skipped", []))
                    content += "\nDo single trivial calls yourself with use_tool; make specialist tasks specific (target + what to test)."
                updates["messages"].append({"role": "user", "content": content})
            except RuntimeError as e:
                # no running loop (defensive — think_node is always in one)
                updates["_current_step"]["tool_output"] = f"Fireteam deploy failed: {e}"
                updates["messages"].append({"role": "user", "content": f"Fireteam deploy failed: {e}"})
    elif action == "switch_skill":
        ss = decision.get("skill_switch") or {}
        to_skill = ss.get("to_skill", "")
        updates["_current_step"] = {}  # pure bookkeeping turn — no execute hop
        updates["attack_path_type"] = to_skill
        updates["_plan_remaining"] = []  # changing course drops the old plan
        updates["messages"].append(
            {
                "role": "user",
                "content": f"SKILL SWITCHED to: {to_skill}. Reason: {ss.get('reason', '')}",
            }
        )

    # ── Apply chain findings ──────────────────────────────────────────
    if chain_findings:
        updates["chain_findings_memory"] = prev_findings + chain_findings

    # ── Stall counter update (with state_grew) ────────────────────────
    stall = _productivity("update_stall_counters")(
        {
            **state,
            "_state_grew_this_turn": state_grew,
            "_chain_advanced_this_turn": productivity.get("verdict") == "new_info" if productivity else False,
            "_diagnostic_progress_this_turn": productivity.get("verdict") == "diagnostic_progress"
            if productivity
            else False,
        },
        iteration,
    )
    updates.update(stall)
    updates["_state_grew_this_turn"] = state_grew

    updates["execution_trace"] = exec_trace
    updates["tested_axes"] = tested_axes

    return updates
