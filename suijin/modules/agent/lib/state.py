"""
Suijin Agent State — Pydantic models for the LangGraph state machine.

Defines the structured state that flows through the agent graph:
think -> execute_tool -> generate_response. Every field is serializable
so state can be checkpointed (MemorySaver) and recovered.

Ported and simplified from redamon/agentic/state.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Knowledge graph labels and DB schema — defined inline
GRAPH_LABELS = {
    "TARGET": "Target",
    "SERVICE": "Service",
    "VULNERABILITY": "Vulnerability",
    "EXPLOIT": "Exploit",
    "CREDENTIAL": "Credential",
    "FLAG": "Flag",
    "SUBDOMAIN": "Subdomain",
    "ENDPOINT": "Endpoint",
    "PORT": "Port",
    "TECHNOLOGY": "Technology",
}
GRAPH_RELATIONSHIPS = {
    "EXPOSES": "EXPOSES",
    "HAS_VULN": "HAS_VULNERABILITY",
    "EXPLOITED_BY": "EXPLOITED_BY",
    "PROVIDES_ACCESS": "PROVIDES_ACCESS_TO",
    "RESOLVES_TO": "RESOLVES_TO",
    "RUNS_ON": "RUNS_ON",
    "USES": "USES_TECHNOLOGY",
}


def build_target_query(target_name: str) -> str:
    """DEPRECATED: Neo4j not connected. Use knowledge_graph.py instead.

    Returns a Cypher query string for reference only.
    The blue team knowledge graph (core/blue/knowledge_graph.py) and
    red team knowledge graph (intel/knowledge_graph.py) are the active stores.
    """
    import warnings

    warnings.warn("build_target_query is deprecated. Use knowledge_graph APIs.", DeprecationWarning, stacklevel=2)
    return f"MATCH (t:Target {{name: '{target_name}'}}) OPTIONAL MATCH (t)-[:EXPOSES]->(s:Service) OPTIONAL MATCH (s)-[:HAS_VULNERABILITY]->(v:Vulnerability) RETURN t, collect(DISTINCT s) as services, collect(DISTINCT v) as vulnerabilities"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# TYPE ALIASES
# =============================================================================

Phase = Literal["informational", "exploitation", "post_exploitation"]
TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]
Priority = Literal["high", "medium", "low"]
ActionType = Literal[
    "use_tool",
    "plan_tools",
    "transition_phase",
    "complete",
    "ask_user",
    "deploy_subagent",
    "switch_skill",
]

_PRIORITY_SYNONYMS = {"info": "low", "critical": "high", "urgent": "high"}


def _coerce_priority(value):
    if isinstance(value, str):
        return _PRIORITY_SYNONYMS.get(value.lower().strip(), value)
    return value


# =============================================================================
# CORE STATE MODELS
# =============================================================================


class TodoItem(BaseModel):
    """LLM-managed task item for tracking progress."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str
    status: TodoStatus = "pending"
    priority: Priority = "medium"
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority_synonyms(cls, v):
        return _coerce_priority(v)


class ExecutionStep(BaseModel):
    """Single step in the Thought-Tool-Output execution trace."""

    step_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    iteration: int
    timestamp: datetime = Field(default_factory=utc_now)
    phase: Phase = "informational"

    thought: str = ""
    reasoning: str = ""

    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None

    tool_output: Optional[str] = None
    output_analysis: Optional[str] = None
    output_summary: Optional[str] = None  # compact one-line for chain context

    success: bool = True
    error_message: Optional[str] = None
    error_class: Optional[str] = None
    duration_ms: Optional[int] = None

    # Productivity verdict (set by the LLM, audited by productivity.py)
    productivity: Optional[dict] = None

    # Structured extracted info
    extracted_info: Optional[dict] = None


class TargetInfo(BaseModel):
    """Accumulated intelligence about the target."""

    primary_target: Optional[str] = None
    target_type: Optional[Literal["ip", "hostname", "domain", "url"]] = None
    ports: List[int] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)
    technologies: List[str] = Field(default_factory=list)
    vulnerabilities: List[str] = Field(default_factory=list)
    credentials: List[dict] = Field(default_factory=list)
    subdomains: List[str] = Field(default_factory=list)
    endpoints: List[str] = Field(default_factory=list)

    def merge_from(self, other: "TargetInfo") -> "TargetInfo":
        return TargetInfo(
            primary_target=other.primary_target or self.primary_target,
            target_type=other.target_type or self.target_type,
            ports=list(set(self.ports + other.ports)),
            services=list(set(self.services + other.services)),
            technologies=list(set(self.technologies + other.technologies)),
            vulnerabilities=list(set(self.vulnerabilities + other.vulnerabilities)),
            credentials=self.credentials + [c for c in other.credentials if c not in self.credentials],
            subdomains=list(set(self.subdomains + other.subdomains)),
            endpoints=list(set(self.endpoints + other.endpoints)),
        )


class PhaseHistoryEntry(BaseModel):
    """Record of a phase transition."""

    phase: Phase
    entered_at: datetime = Field(default_factory=utc_now)
    exited_at: Optional[datetime] = None


class ConversationObjective(BaseModel):
    """Single objective within a continuous conversation."""

    objective_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    completion_reason: Optional[str] = None
    required_phase: Optional[Phase] = None


class PhaseTransitionDecision(BaseModel):
    """Phase transition from LLM decision."""

    to_phase: Phase
    reason: str = ""
    planned_actions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


# =============================================================================
# LLM DECISION MODEL — what the think_node expects the LLM to return
# =============================================================================


class LLMDecision(BaseModel):
    """Structured decision from the LLM think step."""

    action: ActionType = "use_tool"
    thought: str = ""
    reasoning: str = ""

    # For use_tool / plan_tools
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None

    # For plan_tools
    tool_plan: Optional[dict] = None  # {steps: [{tool_name, tool_args, rationale}]}

    # For transition_phase
    phase_transition: Optional[PhaseTransitionDecision] = None

    # For ask_user
    user_question: Optional[dict] = None  # {question, context, format, options}

    # For complete
    completion_reason: Optional[str] = None
    final_summary: Optional[str] = None

    # For switch_skill
    skill_switch: Optional[dict] = None  # {to_skill, reason}

    # Analysis of previous tool output
    output_analysis: Optional[dict] = None  # {productivity, chain_findings, extracted_info}

    # Todo management
    todo_updates: Optional[List[dict]] = None

    @field_validator("tool_args", mode="before")
    @classmethod
    def _ensure_dict(cls, v):
        if v is None:
            return {}
        return v


# =============================================================================
# AGENT STATE — TypedDict for LangGraph (not a Pydantic BaseModel!)
# =============================================================================
# LangGraph uses plain dicts or TypedDicts for state. All internal scratchpad
# fields use underscore prefix — these are NOT Pydantic fields, just dict keys.


# Default factory for a fresh state dict
def new_agent_state(
    *,
    user_id: str = "",
    project_id: str = "",
    session_id: str = "",
    original_objective: str = "",
    max_iterations: int = 100,
    attack_path_type: str = "",
) -> dict:
    """Create a fresh agent state dict with all defaults."""
    return {
        # Session identity
        "user_id": user_id,
        "project_id": project_id,
        "session_id": session_id,
        # Mission
        "original_objective": original_objective,
        "conversation_objectives": [],
        "current_objective_index": 0,
        "objective_history": [],
        # Phase
        "current_phase": "informational",
        "phase_history": [],
        "attack_path_type": attack_path_type,
        # Iteration
        "current_iteration": 0,
        "max_iterations": max_iterations,
        # Execution
        "execution_trace": [],
        "todo_list": [],
        "target_info": {},
        # Chain memory
        "chain_findings_memory": [],
        "chain_failures_memory": [],
        "chain_decisions_memory": [],
        "chain_waves_memory": [],
        # Productivity tracking
        "tested_axes": {},
        "_iterations_since_state_grew": 0,
        "_iterations_since_chain_advance": 0,
        "_state_grew_this_turn": False,
        "_chain_advanced_this_turn": False,
        "_diagnostic_progress_this_turn": False,
        # Current step scratchpad
        "_current_step": {},
        "_tool_result": {},
        # Control signals
        "_abort_transition": False,
        "_guardrail_blocked": False,
        "_just_transitioned_to": None,
        "completion_reason": None,
        # Q&A
        "qa_history": [],
        "pending_questions": [],
        # Messages
        "messages": [],
    }


# =============================================================================
# FORMATTING HELPERS — build prompt sections from state
# =============================================================================


def _truncate(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def format_execution_trace(
    execution_trace: list,
    objectives: list | None = None,
    objective_history: list | None = None,
    current_objective_index: int = 0,
) -> str:
    """Format execution trace for prompt injection."""
    if not execution_trace:
        return "No steps executed yet."

    lines = []
    for _i, step in enumerate(execution_trace[-20:]):  # last 20 steps
        if not isinstance(step, dict):
            continue
        tn = step.get("tool_name", "?")
        ta = step.get("tool_args", {})
        to = step.get("tool_output", "")
        ec = step.get("error_class", "")
        prod = step.get("productivity", {})

        status = "" if step.get("success", True) else ""
        verdict = prod.get("verdict", "") if isinstance(prod, dict) else ""

        lines.append(
            f"Step {step.get('iteration', '?')}: {status} {tn} ({verdict or ec or 'ok'}) -> {_truncate(str(ta), 100)}"
        )
        if to:
            lines.append(f"  Output: {_truncate(to, 300)}")

    return "\n".join(lines)


def format_todo_list(todo_list: list) -> str:
    """Format todo list for prompt injection."""
    if not todo_list:
        return "No tasks tracked."

    lines = []
    status_icons = {
        "pending": "",
        "in_progress": "",
        "completed": "[done]",
        "blocked": "",
    }
    for item in todo_list:
        if not isinstance(item, dict):
            continue
        icon = status_icons.get(item.get("status", "pending"), "")
        priority = item.get("priority", "medium")
        desc = item.get("description", "")[:100]
        uid = item.get("id", "")
        # the id renders so todo_updates can REFERENCE tasks — it was
        # matched by id while the prompt never showed one
        lines.append(f"  {icon} [{priority}] {desc}" + (f"  (id: {uid})" if uid else ""))

    return "\n".join(lines) if lines else "No tasks tracked."


def format_chain_context(
    chain_findings: list,
    chain_failures: list,
    chain_decisions: list,
    execution_trace: list,
    chain_waves: list | None = None,
) -> str:
    """Build a compact chain-context summary for the think prompt.

    This is the primary "memory" the LLM sees each turn — recent findings,
    failures (with error_class), decisions, and the last few execution steps
    with productivity verdicts.
    """
    parts = []

    # Recent findings
    if chain_findings:
        recent = chain_findings[-3:]
        parts.append("## Recent Findings")
        for f in recent:
            if isinstance(f, dict):
                parts.append(
                    f"- [{f.get('severity', 'info')}] {f.get('title', f.get('finding_type', '?'))}: "
                    f"{_truncate(f.get('evidence', ''), 120)}"
                )

    # Recent failures with error class
    if chain_failures:
        recent_f = chain_failures[-3:]
        parts.append("## Recent Failures")
        for f in recent_f:
            if isinstance(f, dict):
                ec = f.get("error_class", "unknown")
                parts.append(f"- [{ec}] {f.get('tool_name', '?')}: {_truncate(f.get('error_message', ''), 120)}")

    # Recent execution trace with productivity
    if execution_trace:
        recent = execution_trace[-6:]
        parts.append("## Recent Steps")
        for s in recent:
            if not isinstance(s, dict):
                continue
            prod = s.get("productivity", {})
            verdict = prod.get("verdict", "?") if isinstance(prod, dict) else "?"
            ec = s.get("error_class", "")
            tag = verdict if verdict != "?" else (ec or "ok")
            parts.append(
                f"- [{tag}] iter {s.get('iteration', '?')}: "
                f"{s.get('tool_name', '?')} -> {_truncate(s.get('output_summary', s.get('tool_output', '')), 200)}"
            )

    return "\n\n".join(parts) if parts else "No chain context yet."


def format_qa_history(qa_history: list) -> str:
    """Format Q&A history for the prompt."""
    if not qa_history:
        return ""
    lines = ["## Previous Q&A"]
    for entry in qa_history[-5:]:
        if isinstance(entry, dict):
            q = entry.get("question", entry.get("question_text", "?"))
            a = entry.get("answer", "(unanswered)")
            lines.append(f"- Q: {_truncate(str(q), 150)}\n  A: {_truncate(str(a), 150)}")
    return "\n".join(lines)


def format_objective_history(objective_history: list) -> str:
    """Format completed objectives."""
    if not objective_history:
        return ""
    lines = ["## Completed Objectives"]
    for obj in objective_history[-5:]:
        if isinstance(obj, dict):
            lines.append(f"- [{obj.get('success', True) and '' or ''}] {_truncate(obj.get('content', ''), 150)}")
    return "\n".join(lines)
