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
                merged[k] = out[-25:]
            else:
                merged[k] = (merged[k] + v)[-25:]  # cap at 25 to prevent OOM
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
        obj = state.get("_objective", state.get("original_objective", ""))
        result = initialize_node(state, objective=obj, config={"max_iterations": self.max_iterations})
        if "original_objective" not in result:
            result["original_objective"] = obj
        # Reset circuit breaker
        result["_consecutive_failures"] = 0
        return result

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
            result = await think_node(state, generate_fn=self.generate_fn)
            # Circuit breaker: track consecutive provider/parse failures
            if result.get("completion_reason") in ("provider_failure", "parse_failure", "llm_error"):
                fails = state.get("_consecutive_failures", 0) + 1
                result["_consecutive_failures"] = fails
            else:
                result["_consecutive_failures"] = 0

            # ── Supervisor check (runs every N iterations) ──────────
            supervisor_interval = 5  # can be loaded from config
            iteration = result.get("current_iteration", state.get("current_iteration", 0))
            if iteration > 0 and iteration % supervisor_interval == 0:
                try:
                    from suijin.modules.agent.lib.supervisor import analyze_trace, analyze_trace_with_llm

                    trace = result.get("execution_trace", state.get("execution_trace", []))

                    # Pattern-based check (zero cost, always runs)
                    guidance = analyze_trace(trace[-15:])
                    if guidance:
                        logger.info(f"Supervisor pattern intervention at iteration {iteration}: {guidance[:80]}")
                        result.setdefault("messages", []).append(
                            {
                                "role": "user",
                                "content": f"SUPERVISOR: {guidance}",
                            }
                        )
                        result["_supervisor_guidance"] = guidance
                    else:
                        # LLM-powered deep analysis (runs less frequently)
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
            if iteration > 0 and iteration % 4 == 0:
                try:
                    from suijin.modules.redteam.lib.intel.oracle import detect_anomaly, generate_hypotheses_async

                    trace = result.get("execution_trace", state.get("execution_trace", []))
                    if trace:
                        last_step = trace[-1]
                        tool_output = str(last_step.get("tool_output", ""))
                        if detect_anomaly(tool_output):
                            hypotheses = await generate_hypotheses_async(tool_output, state, self.generate_fn)
                            if hypotheses:
                                result.setdefault("messages", []).append(
                                    {
                                        "role": "user",
                                        "content": f"ORACLE: Anomalous response detected. Hypotheses: {hypotheses}",
                                    }
                                )
                                result["_oracle_hypotheses"] = hypotheses
                except Exception as e:
                    logger.debug(f"Oracle check skipped: {e}")

            # ── Drift analysis ─────────────────────────────────────
            if iteration > 0 and iteration % 7 == 0:
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

            # ── Session persistence (save every 5 iterations) ──────
            if iteration > 0 and iteration % 5 == 0:
                try:
                    from suijin.modules.agent.lib.engagement import save_session_state

                    merged = {**state, **result}
                    save_session_state(merged)
                except Exception as e:
                    logger.warning(f"Session save failed: {e}")

            return result
        except Exception as e:
            logger.exception("think crashed")
            fails = state.get("_consecutive_failures", 0) + 1
            return {
                "_current_step": {"thought": f"Think error: {e}", "tool_name": "none", "tool_args": {}},
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
            return await execute_tool_node(
                state,
                route_tool_fn=self.route_tool_fn,
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
        resume_from_recovery: bool = False,
    ) -> dict:
        """Run the agent for an objective. Returns the final state dict.

        Args:
            objective: The red-team objective (target URL, IP, or goal).
            thread_id: Checkpointer thread ID for session continuity.
            user_id: User identifier.
            project_id: Project identifier.
            session_id: Session identifier.
            resume_from_recovery: If True, restore state from operation_state_recovery.json.

        Returns:
            Final agent state dict with execution_trace, target_info, messages.
        """
        self._build()

        # Initialize engagement schema and recovery
        from suijin.modules.agent.lib.engagement import (
            clear_recovery_state,
            has_recovery_state,
            load_engagement_schema,
            load_session_state,
            save_engagement_schema,
        )

        initial_state = {
            "_run_config": self.run_config,
            "_objective": objective,
            "user_id": user_id,
            "project_id": project_id,
            "session_id": session_id,
        }

        # Check for crash recovery
        if resume_from_recovery and has_recovery_state():
            recovery = load_session_state()
            if recovery and recovery.get("objective") == objective:
                logger.info(f"Resuming from recovery: iteration {recovery.get('iteration', 0)}")
                initial_state["_recovery_data"] = recovery
                initial_state["original_objective"] = recovery["objective"]
                initial_state["current_phase"] = recovery.get("phase", "informational")
                initial_state["current_iteration"] = recovery.get("iteration", 0)
                initial_state["total_cost_usd"] = recovery.get("cost_usd", 0.0)
                initial_state["findings"] = recovery.get("findings", [])
                initial_state["flags_found"] = recovery.get("flags_found", [])
                initial_state["todo_list"] = recovery.get("todo_list", [])
                initial_state["chain_findings_memory"] = recovery.get("chain_findings_memory", [])
                initial_state["knowledge_graph"] = recovery.get("knowledge_graph_snapshot", {})
                if recovery.get("messages"):
                    initial_state["messages"] = [
                        {"role": "system", "content": "Session restored from crash recovery. Resume operations."}
                    ] + recovery["messages"]

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
            logger.exception("Agent graph crashed — saving recovery state")
            try:
                from suijin.modules.agent.lib.engagement import save_session_state

                save_session_state(initial_state)
            except Exception:
                pass
            raise

        # Clear recovery on clean completion
        clear_recovery_state()

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
