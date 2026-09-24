"""
Suijin Agent Graph — LangGraph state machine for autonomous red teaming.

Replaces the synchronous while-loop in redteamer.py with a proper
LangGraph state machine that provides:

- Structured state flow (initialize -> think -> execute_tool -> think -> ...)
- Automatic checkpointing via MemorySaver
- Clean separation of reasoning (think) and action (execute_tool)
- Productivity scoring and stall detection
- Structured output parsing with retry

Architecture:
    initialize ──-> think ──-> execute_tool ──-> think (loop)
                      │                          │
                      └──-> generate_response ──-> END
"""

from __future__ import annotations

import contextlib
import logging

# langgraph's checkpoint module emits a PendingDeprecation advisory at
# import time — and langchain_core PREPENDS a ('default', category=
# LangChainPendingDeprecationWarning) filter during its own import, which
# lands ABOVE any message filter we set earlier (that's why it kept leaking
# in the field despite filters in suijin/__init__, cli and main). Immune
# order: import langchain_core FIRST (its prepend happens), THEN put our
# category-ignore on top, THEN import langgraph.
import warnings as _warnings
from datetime import datetime, timezone
from typing import Annotated, Callable, Optional

with _warnings.catch_warnings():
    import langchain_core  # noqa: F401 — imported for its warning-filter side effect
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    _warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

from suijin.modules.agent.lib.nodes.execute_tool_node import execute_tool_node
from suijin.modules.agent.lib.nodes.generate_response_node import generate_response_node
from suijin.modules.agent.lib.nodes.initialize_node import initialize_node
from suijin.modules.agent.lib.nodes.think_node import think_node

logger = logging.getLogger(__name__)


# ── State merge function ──────────────────────────────────────────────
# LangGraph v1.x with plain dict uses last-write-wins. We need a shallow
# merge so nodes can return partial updates and the state accumulates.


def _merge_state(left: dict, right: dict) -> dict:
    """Shallow merge. Lists like 'messages' accumulate with caps.

    execution_trace: steps are keyed by iteration — an update whose step
    iteration already exists REPLACES it in place (execute_tool_node
    back-fills success/error_class/tool_output onto the step think_node
    opened); new iterations append. Cap 25 either way."""
    # Context cap configurable (2026-09-12): context_cap, default 25.
    # A QA verification pass sets 12 — measured on the kestrel runs, the
    # 25+25 re-read was most of the ~20k tokens per iteration, and every
    # token is turn latency on a thinking model.
    cap = 25
    try:
        rc = (left or {}).get("_run_config") or {}
        cap = int(rc.get("context_cap", 25) or 25)
    except Exception:  # noqa: BLE001 — the cap may never break the merge
        cap = 25
    merged = dict(left)
    for k, v in right.items():
        if k in ("messages", "execution_trace") and k in merged and isinstance(merged[k], list) and isinstance(v, list):
            if k == "execution_trace":
                out = list(merged[k])
                by_iter = {s.get("iteration"): i for i, s in enumerate(out) if isinstance(s, dict)}
                for s in v:
                    if isinstance(s, dict) and s.get("iteration") in by_iter:
                        out[by_iter[s["iteration"]]] = s  # back-fill / update
                    else:
                        by_iter[s.get("iteration")] = len(out)
                        out.append(s)
                merged[k] = out[-cap:]
            else:
                merged[k] = (merged[k] + v)[-cap:]  # capped to prevent OOM
        else:
            merged[k] = v
    return merged


# Type for LangGraph: Annotated[dict, merge_fn] gives us accumulating state.
MergeableDict = Annotated[dict, _merge_state]


class SuijinAgentGraph:
    """LangGraph-based autonomous red team agent.

    Usage:
        agent = SuijinAgentGraph(generate_fn=providers.generate_async,
                                  route_tool_fn=tools.route_tool,
                                  tool_catalog_fn=tools.get_tool_catalog)
        await agent.run("Find vulnerabilities on http://target.com")
    """

    def __init__(
        self,
        *,
        generate_fn: Callable,
        route_tool_fn: Callable,
        max_iterations: int = 9999,
        checkpoint_saver=None,
        run_config: dict | None = None,
    ):
        self.generate_fn = generate_fn
        self.route_tool_fn = route_tool_fn
        self.max_iterations = max_iterations
        self.run_config = run_config or {}
        self.checkpointer = checkpoint_saver or MemorySaver()
        self._health_reported: set = set()  # component-health dedupe (once per failure)
        self._graph = None
        self._built = False  # per-instance, no module-level state

    def _route_after_think(self, state: dict) -> str:
        # Circuit breaker: 3+ consecutive failures -> force end
        if state.get("_consecutive_failures", 0) >= 3:
            return "generate_response"
        if state.get("completion_reason"):
            return "generate_response"
        step = state.get("_current_step", {})
        if step.get("tool_name"):
            return "execute_tool"
        return "think"

    def _build(self):
        """Build the LangGraph state machine — lean, no supervisor."""
        if self._built:
            return

        builder = StateGraph(MergeableDict)
        builder.add_node("initialize", self._initialize)
        builder.add_node("think", self._think)
        builder.add_node("execute_tool", self._execute_tool)
        builder.add_node("generate_response", self._generate_response)

        builder.add_edge(START, "initialize")
        builder.add_edge("initialize", "think")

        builder.add_conditional_edges(
            "think",
            self._route_after_think,
            {
                "execute_tool": "execute_tool",
                "generate_response": "generate_response",
                "think": "think",
            },
        )

        builder.add_edge("execute_tool", "think")
        builder.add_edge("generate_response", END)

        self._graph = builder.compile(checkpointer=self.checkpointer)
        self._built = True

    # ── Node wrappers ──────────────────────────────────────────────

    async def _initialize(self, state: dict) -> dict:
        # the ONE node that previously had no crash wrapper — an exception
        # here killed the engagement at boot (every other node degrades)
        try:
            obj = state.get("_objective", state.get("original_objective", ""))
            result = initialize_node(state, objective=obj, config={"max_iterations": self.max_iterations})
            if "original_objective" not in result:
                result["original_objective"] = obj
            # Reset circuit breaker
            result["_consecutive_failures"] = 0
            return result
        except Exception as e:  # noqa: BLE001 — boot must degrade, not die
            logger.exception("initialize crashed")
            return {
                "current_phase": "informational",
                "current_iteration": 0,
                "messages": [
                    {"role": "user", "content": f"SYSTEM: initialize node crashed: {e} — continuing with minimal state"}
                ],
                "execution_trace": [],
                "todo_list": [],
                "target_info": {},
                "_consecutive_failures": 0,
            }

    async def _think(self, state: dict) -> dict:
        # Cost governor (D27): warn near budget, hard-stop past it — before
        # the next LLM call spends more.
        try:
            from suijin.modules.platform.lib.governor import budget_guard

            _cfg = state.get("_run_config") or self.run_config
            _guard = budget_guard(_cfg)
            if _guard and _guard.startswith("COST LIMIT REACHED"):
                return {
                    "messages": [{"role": "assistant", "content": _guard}],
                    "completion_reason": "budget_exhausted",
                    "final_summary": _guard,
                    "current_iteration": state.get("current_iteration", 0) + 1,
                }
            if _guard:
                state.setdefault("messages", []).append({"role": "user", "content": f"SYSTEM: {_guard}"})
        except Exception:  # noqa: BLE001 — budgeting must never break the loop
            pass
        try:
            result = await think_node(
                state, generate_fn=self.generate_fn, config=self.run_config, route_tool_fn=self.route_tool_fn
            )
            # Circuit breaker: track consecutive provider/parse failures
            if result.get("completion_reason") in ("provider_failure", "parse_failure", "llm_error"):
                fails = state.get("_consecutive_failures", 0) + 1
                result["_consecutive_failures"] = fails
            else:
                result["_consecutive_failures"] = 0

            # ── No-progress breaker: valid-but-empty decisions (use_tool
            # with no tool_name, endless bookkeeping no-ops) used to loop
            # forever with ZERO structural stop — every turn billed. After
            # a nudge the loop gets ONE last chance, then a clean stop.
            _step = result.get("_current_step") or {}
            _did_work = (
                bool(_step.get("tool_name"))
                or bool(result.get("completion_reason"))
                or result.get("_just_transitioned_to")
            )
            if _did_work:
                result["_no_progress_turns"] = 0
            else:
                _np = int(state.get("_no_progress_turns", 0)) + 1
                result["_no_progress_turns"] = _np
                if _np == 4:
                    result.setdefault("messages", []).append(
                        {
                            "role": "user",
                            "content": (
                                "SYSTEM: four consecutive turns produced no tool action and no transition. "
                                "Decisively use_tool (a real tool_name) or complete with a report — "
                                "repeated empty turns will end this engagement."
                            ),
                        }
                    )
                elif _np >= 7 and not result.get("completion_reason"):
                    result["completion_reason"] = "error: no-progress loop breaker (7 empty turns)"
                    result["final_summary"] = "Engagement stopped: repeated decisions executed nothing."

            # ── Component health sweep: old parts rot silently ─────────
            # Every health_interval iterations a canary dispatch proves
            # the tool plane still answers; a DEAD component surfaces as
            # a visible system note (once per failure — no spam). Rot is
            # otherwise invisible until the operator needs the tool.
            _health_iv = int((state.get("_run_config") or self.run_config or {}).get("health_interval", 25) or 0)
            _health_iter = int(state.get("current_iteration") or 0)
            if _health_iv > 0 and _health_iter > 0 and _health_iter % _health_iv == 0:
                try:
                    from suijin.modules.tools.lib.dispatch import route_tool as _rt

                    _canary = str(_rt("job_list", {}, {}) or "")
                    if (_canary.startswith("Error:") or "unknown tool" in _canary.lower()) and (
                        "job_list" not in self._health_reported
                    ):
                        self._health_reported.add("job_list")
                        result.setdefault("messages", []).append(
                            {
                                "role": "user",
                                "content": (
                                    "SYSTEM (component health): the job registry answers with an error "
                                    f"({_canary[:120]}). Background jobs may be dead — prefer foreground "
                                    "tools until it recovers."
                                ),
                            }
                        )
                except Exception as _he:  # noqa: BLE001 — health checks never break the run
                    if "dispatch" not in self._health_reported:
                        self._health_reported.add("dispatch")
                        result.setdefault("messages", []).append(
                            {
                                "role": "user",
                                "content": f"SYSTEM (component health): tool dispatch raised {_he!r} — report this.",
                            }
                        )

            # ── Mode governor: surface queue + recon→exploit switch ──
            try:
                from suijin.modules.agent.lib import mode_governor as _mg

                queue = _mg.update_queue(state, result)
                if queue != (state.get("_attack_queue") or []):
                    result["_attack_queue"] = queue
                _mg.update_foothold(state, result)
                # coverage awareness: wall-clock pressure on unexamined
                # items (silent without a deadline — operator runs are
                # unchanged)
                _mg.coverage_pressure(state, result, queue)
                if int(result.get("current_iteration") or 0) <= 1 and "_prior_confirmed" not in state:
                    with contextlib.suppress(Exception):
                        from suijin.modules.agent.lib.attack_memory import what_worked

                        prior = what_worked(state.get("_objective") or self.run_config.get("_objective") or "")
                        if prior:
                            result["_prior_confirmed"] = prior
                        # cross-engagement operational memory: prior runs against
                        # this target (computed at boot, discarded for 361 sessions)
                        from suijin.modules.agent.lib import memory as _mem
                        from suijin.modules.agent.lib.attack_memory import target_key as _tk

                        _rec = _mem.recall(
                            _tk(state.get("_objective") or self.run_config.get("_objective") or ""), limit=3
                        )
                        if _rec and "no memory of" not in _rec:
                            result["_target_recall"] = _rec
                        with contextlib.suppress(Exception):
                            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR as _WS

                            _lg = _WS / "outputs" / "bench" / "learnings.md"
                            if _lg.is_file():
                                _notes = [ln for ln in _lg.read_text().splitlines() if ln.strip()][-3:]
                                if _notes:
                                    result["_gym_notes"] = _notes
                directive = _mg.govern(state, state.get("_run_config") or self.run_config)
                if directive:
                    msgs = directive.pop("messages", [])
                    for k, v in directive.items():
                        result[k] = v
                    result.setdefault("messages", []).extend(msgs)  # APPEND — think's messages must survive the merge
                # Weaponizer (propose-only): a CONFIRMED catalog_exploit earns
                # a ready-to-fire escalation task in context — the model
                # decides, one turn cost, no auto-spawn.
                with contextlib.suppress(Exception):
                    from suijin.modules.agent.lib.weaponizer import propose_for

                    already = set(state.get("_escalations_proposed") or [])
                    prop = propose_for(result, already)
                    if prop:
                        msg, key = prop
                        already.add(key)
                        result["_escalations_proposed"] = sorted(already)[-50:]
                        result.setdefault("messages", []).append({"role": "user", "content": msg})
            except Exception as _mg_err:  # noqa: BLE001 — never break the loop
                logger.debug(f"mode governor skipped: {_mg_err}")

            # ── Supervisor check (runs every N iterations) ──────────
            with contextlib.suppress(Exception):
                supervisor_interval = int((state.get("_run_config") or self.run_config).get("supervisor_interval", 5))
                # deep-analysis cadence configurable (2026-09-12): 0 = off
                _deep_iv = int((state.get("_run_config") or self.run_config).get("supervisor_deep_interval", 15) or 0)
            iteration = result.get("current_iteration", state.get("current_iteration", 0))
            if iteration > 0 and iteration % supervisor_interval == 0:
                try:
                    from suijin.modules.agent.lib.supervisor import analyze_trace, analyze_trace_with_llm

                    trace = result.get("execution_trace", state.get("execution_trace", []))

                    # Pattern-based check (zero cost, always runs; iteration
                    # enables per-detector cooldowns so nothing nags twice)
                    guidance = analyze_trace(trace[-15:], iteration=iteration)
                    if guidance:
                        # Wrap-up gate: "generate your report and complete"
                        # is forbidden while untried attack surfaces remain
                        # — quitting with attack debt is the timidity bug.
                        try:
                            from suijin.modules.agent.lib import mode_governor as _mg

                            _open = _mg.untried(state.get("_attack_queue") or result.get("_attack_queue") or [])
                        except Exception:  # noqa: BLE001
                            _open = []
                        # findings bypass (2026-09-15): a run with confirmed
                        # findings completes — "1 vuln = done" outranks the
                        # wrap-up gate, same as the think-node completion gate
                        if (
                            "generate your report" in guidance
                            and _open
                            and not (state.get("findings") or result.get("findings"))
                        ):
                            guidance = (
                                f"Recon yield is exhausted but {len(_open)} attack surfaces remain UNTRIED — "
                                "switch to exploitation and work the queue before any report. "
                                f"Top: {', '.join(str(s['surface'])[:50] for s in _open[:3])}"
                            )
                        logger.info(f"Supervisor pattern intervention at iteration {iteration}: {guidance[:80]}")
                        result.setdefault("messages", []).append(
                            {
                                "role": "user",
                                "content": f"SUPERVISOR: {guidance}",
                            }
                        )
                        result["_supervisor_guidance"] = guidance
                    elif _deep_iv > 0 and iteration % _deep_iv == 0:
                        # LLM deep analysis — RARELY (was: every silent check,
                        # i.e. every 5th iteration — constant chatter that
                        # derailed exploitation runs). Every 15th, max.
                        try:
                            llm_guidance = await analyze_trace_with_llm(trace, state, self.generate_fn)
                            if llm_guidance:
                                logger.info(f"Supervisor LLM insight at iteration {iteration}")
                                result.setdefault("messages", []).append(
                                    {
                                        "role": "user",
                                        "content": f"SUPERVISOR (deep analysis): {llm_guidance}",
                                    }
                                )
                                result["_supervisor_guidance"] = llm_guidance
                        except Exception as llm_err:
                            logger.debug(f"LLM supervisor skipped: {llm_err}")

                except Exception as e:
                    logger.warning(f"Supervisor check failed: {e}")

            # ── Oracle anomaly detection ───────────────────────────
            # Cadence configurable (2026-09-12): oracle_interval, 0 = off.
            # Default 4 preserves operator behavior; a QA verification pass
            # disables it — its validation probe is an LLM call the worklist
            # does not need (measured: supervision added ~1.5 calls/iter).
            _oracle_iv = int((state.get("_run_config") or self.run_config or {}).get("oracle_interval", 4) or 0)
            if _oracle_iv > 0 and iteration > 0 and iteration % _oracle_iv == 0:
                try:
                    from suijin.modules.redteam.lib.intel.oracle import detect_anomaly, generate_hypotheses_async

                    trace = result.get("execution_trace", state.get("execution_trace", []))
                    if trace:
                        last_step = trace[-1]
                        tool_output = str(last_step.get("tool_output", ""))
                        # Oracle scope: RESPONSE triage. It parses HTTP-response
                        # anomalies (status/baseline/error text) — firing it on
                        # terminal dumps and JS bundles (field run: a 518KB
                        # bundle 'anomaly' produced an irrelevant SQLi
                        # hypothesis mid-recon) is pure interference.
                        if last_step.get("tool_name") == "http_request" and detect_anomaly(tool_output):
                            hypotheses = await generate_hypotheses_async(tool_output, state, self.generate_fn)
                            if hypotheses:
                                # ACTUATION (not homework): the top hypothesis
                                # carrying a validation_payload gets ONE
                                # auto-fired probe — paced by http_request's
                                # own limiter — and the EVIDENCE lands in the
                                # conversation. The agent reacts to results,
                                # not suggestions.
                                _fired = ""
                                try:
                                    import asyncio as _aio
                                    import json as _json

                                    for _h in hypotheses or []:
                                        _vp = str((_h or {}).get("validation_payload") or "").strip()
                                        _target = str(last_step.get("tool_args", {}).get("url") or "")
                                        if _vp and _target and "http" in _vp.lower():
                                            _probe = {
                                                "method": "GET",
                                                "url": _target,
                                                "body": _vp if "{" in _vp or "=" in _vp else "",
                                                "headers": {"Content-Type": "application/json"}
                                                if _vp.startswith("{")
                                                else None,
                                            }
                                            try:
                                                _payload = _json.loads(_vp)
                                                if isinstance(_payload, dict):
                                                    _probe.update(
                                                        {
                                                            k: v
                                                            for k, v in _payload.items()
                                                            if k in ("method", "url", "headers", "body")
                                                        }
                                                    )
                                            except Exception:  # noqa: BLE001 — payload may be raw text
                                                pass
                                            _out = await _aio.wait_for(
                                                _aio.to_thread(self.route_tool_fn, "http_request", _probe, {}),
                                                timeout=45,
                                            )
                                            _fired = f"\nORACLE PROBE FIRED ({_probe.get('method')} {_probe.get('url')}) → {str(_out)[:400]}"
                                            break
                                except Exception as _ofire:  # noqa: BLE001 — actuation is best-effort
                                    logger.debug(f"oracle actuation skipped: {_ofire}")
                                # verified-evidence memory: the fired probe's output is
                                # EVIDENCE — record it as a KG constraint (the in-graph
                                # oracle hook used to produce messages only; the KG
                                # writer existed solely on the standalone CLI path)
                                if _fired:
                                    with contextlib.suppress(Exception):
                                        from suijin.modules.loader import load_local_module
                                        from suijin.modules.redteam.lib.intel.oracle import (
                                            _map_hypothesis_to_constraint,
                                        )

                                        _kg = load_local_module("knowledge_graph")
                                        _h0 = str((hypotheses or [{}])[0].get("hypothesis", "anomaly"))
                                        _kg.add_constraint(
                                            target=str(last_step.get("tool_args", {}).get("url") or "unknown"),
                                            constraint_type=_map_hypothesis_to_constraint(_h0),
                                            rule=str((hypotheses or [{}])[0].get("validation_payload") or _h0)[:200],
                                            evidence=str(_out or "")[:300],
                                            confidence=0.6,  # probe evidence, not a verified exploit
                                        )
                                result.setdefault("messages", []).append(
                                    {
                                        "role": "user",
                                        "content": f"ORACLE: Anomalous response detected. Hypotheses: {hypotheses}{_fired}",
                                    }
                                )
                                result["_oracle_hypotheses"] = hypotheses
                except Exception as e:
                    logger.debug(f"Oracle check skipped: {e}")

            # ── Drift analysis ─────────────────────────────────────
            # Cadence configurable (2026-09-12): drift_interval, 0 = off.
            _drift_iv = int((state.get("_run_config") or self.run_config or {}).get("drift_interval", 7) or 0)
            if _drift_iv > 0 and iteration > 0 and iteration % _drift_iv == 0:
                try:
                    from suijin.modules.redteam.lib.intel.drift_analyser import analyse_drift

                    objective = state.get("original_objective", "")
                    trace = result.get("execution_trace", state.get("execution_trace", []))
                    recent_actions = [
                        f"{s.get('tool_name', '?')}: {str(s.get('thought', ''))[:100]}" for s in trace[-10:]
                    ]
                    drift_result = analyse_drift(objective, recent_actions)
                    if drift_result.get("drift_detected"):
                        suggestions = drift_result.get("suggestions", [])
                        msg = f"DRIFT WARNING: {drift_result.get('drift_causes', ['Unknown'])[0]}. Suggestions: {'; '.join(suggestions[:3])}"
                        result.setdefault("messages", []).append(
                            {
                                "role": "user",
                                "content": msg,
                            }
                        )
                        result["_drift_warning"] = drift_result
                except Exception as e:
                    logger.debug(f"Drift analysis skipped: {e}")

            return result
        except Exception as e:
            logger.exception("think crashed")
            fails = state.get("_consecutive_failures", 0) + 1
            return {
                # tool_name MUST stay falsy (invariant #10): the old "none"
                # was truthy → the router dispatched execute_tool → a wasted
                # TOOL NOT FOUND turn after every think crash
                "_current_step": {"thought": f"Think error: {e}", "tool_name": "", "tool_args": {}},
                "messages": [
                    {
                        "role": "user",
                        "content": f"SYSTEM: think node crashed: {e}. The next action should use 'complete' to report the error.",
                    }
                ],
                "_consecutive_failures": fails,
                "completion_reason": "node_crash" if fails >= 3 else None,
            }

    async def _execute_tool(self, state: dict) -> dict:
        try:
            # the LIVE config rides along (mode/policy gates read it): the
            # old {} made check_mode_restrictions structurally unreachable
            # from real engagements — HITL approvals existed but nothing
            # could ever trigger them
            _cfg = self.run_config or {}

            def _route(name, args, _c=None):
                return self.route_tool_fn(name, args, _cfg)

            _route._suijin_cfg = _cfg  # subagents inherit the live config

            return await execute_tool_node(
                state,
                route_tool_fn=_route,
            )
        except Exception as e:
            logger.exception("execute_tool crashed")
            return {
                "_tool_result": {"success": False, "error": str(e)},
                "_current_step": {"tool_output": f"Node error: {e}", "success": False, "error_class": "node_crash"},
                "messages": [
                    {"role": "user", "content": f"SYSTEM: execute_tool node crashed: {e}. Continue with next action."}
                ],
            }

    async def _generate_response(self, state: dict) -> dict:
        try:
            return await generate_response_node(
                state,
                generate_fn=self.generate_fn,
            )
        except Exception as e:
            logger.exception("generate_response crashed")
            return {
                "completion_reason": f"error: {e}",
                "final_summary": f"Agent crashed during response generation: {e}",
                "messages": [{"role": "user", "content": f"SYSTEM: generate_response node crashed: {e}"}],
            }

    # ── Public API ──────────────────────────────────────────────────

    async def run(
        self,
        objective: str,
        *,
        thread_id: str = "default",
        user_id: str = "local",
        project_id: str = "default",
        session_id: str = "",
    ) -> dict:
        """Run the agent for an objective. Returns the final state dict.

        Args:
            objective: The red-team objective (target URL, IP, or goal).
            thread_id: Checkpointer thread ID for session continuity.
            user_id: User identifier.
            project_id: Project identifier.
            session_id: Session identifier.

        Returns:
            Final agent state dict with execution_trace, target_info, messages.
        """
        self._build()

        # Initialize the engagement schema (crash durability is the .sje
        # bundle's job — the recovery.json machinery was removed)
        from suijin.modules.agent.lib.engagement import load_engagement_schema, save_engagement_schema

        initial_state = {
            "_run_config": self.run_config,
            "_objective": objective,
            "user_id": user_id,
            "project_id": project_id,
            "session_id": session_id,
        }

        # Init engagement schema
        schema = load_engagement_schema()
        schema["objective"] = objective
        schema["created_at"] = schema.get("created_at") or datetime.now(timezone.utc).isoformat()
        save_engagement_schema(schema)

        config = {
            "recursion_limit": self.max_iterations * 5,
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
                "project_id": project_id,
                "session_id": session_id,
            },
        }

        logger.info(f"Starting agent run: objective='{objective[:80]}...' thread={thread_id}")

        try:
            final_state = await self._graph.ainvoke(initial_state, config)
        except Exception:
            # crash durability is the crash-saver's .sje (redteamer) —
            # mid-run snapshots were removed with the recovery machinery
            logger.exception("Agent graph crashed")
            raise

        logger.info(
            f"Agent run complete: {final_state.get('current_iteration', 0)} iterations, "
            f"phase={final_state.get('current_phase', '?')}"
        )

        return final_state

    async def resume(
        self,
        *,
        thread_id: str = "default",
        user_id: str = "local",
        project_id: str = "default",
        session_id: str = "",
    ) -> dict:
        """Resume a checkpointed session. The graph will pick up where it left off."""
        self._build()

        config = {
            "recursion_limit": self.max_iterations * 5,
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
                "project_id": project_id,
                "session_id": session_id,
            },
        }

        # Pass None as input — LangGraph will resume from checkpoint
        final_state = await self._graph.ainvoke(None, config)

        return final_state

    def get_state(self, thread_id: str = "default") -> Optional[dict]:
        """Get the current checkpointed state for a thread."""
        self._build()
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = self._graph.get_state(config)
        if snapshot and snapshot.values:
            return dict(snapshot.values)
        return None
