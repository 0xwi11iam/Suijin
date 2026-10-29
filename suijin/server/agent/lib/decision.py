"""The Decision boundary — model output is parsed EXACTLY ONCE.

The live-proved failure class (2026-10-29): the same model decision was
re-read at every layer with different assumptions — think_node read
`question`, execute_tool_node read `tool_args.question`, and the two
sites disagreed on the fallback. The ask loop that followed was not bad
luck; it was structural.

THE RULE (the reliability standard, 2026-10-30):
  - normalize_decision() is the ONLY place that tolerates model drift
  - every downstream consumer reads the normalized shape — no second
    opinion, no second fallback, no second bug
  - new code uses the typed Decision; the graph state carries the
    normalized dict (langgraph state is serializable dicts)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Action(str, Enum):
    """Every action the model emits. Values are the wire strings. This
    enum is ADVISORY for typed consumers — normalize_decision passes
    UNKNOWN actions through unchanged (degrading them broke switch_skill,
    a real action the first enum draft missed)."""

    USE_TOOL = "use_tool"
    PLAN_TOOLS = "plan_tools"
    ASK_OPERATOR = "ask_operator"
    ASK_USER = "ask_user"
    TRANSITION_PHASE = "transition_phase"
    COMPLETE = "complete"
    DEPLOY_SUBAGENT = "deploy_subagent"
    SWITCH_SKILL = "switch_skill"
    WRITE_NOTE = "write_note"


def _as_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def normalize_decision(d: dict | None) -> dict:
    """Return the decision dict with EVERY canonical key guaranteed:

    action, tool_name, tool_args, question, thought, reasoning

    Tolerates the drift models actually emit (proven by field trails):
      question nested under tool_args/args/q   (the ask-loop, seq 71)
      tool_name under tool                     (seen in retries)
      tool_args under args
    All other keys pass through untouched.
    """
    d = _as_dict(d)
    out = dict(d)

    # action — normalized to a bare string; UNKNOWN values pass through
    # (switch_skill-class actions must reach their handlers intact)
    out["action"] = str(out.get("action") or "use_tool").strip()

    # tool_name — accept the tool shorthand
    out["tool_name"] = str(out.get("tool_name") or out.get("tool") or "").strip()

    # tool_args — accept args; merge nested tool/args keys conservatively
    tool_args = _as_dict(out.get("tool_args")) or _as_dict(out.get("args"))
    out["tool_args"] = tool_args

    # question — THE ask-loop fix, owned HERE and only here
    q = out.get("question")
    if not (isinstance(q, str) and q.strip()):
        q = ""
        for holder in (tool_args, _as_dict(out.get("args"))):
            cand = holder.get("question") or holder.get("q")
            if isinstance(cand, str) and cand.strip():
                q = cand
                break
    out["question"] = q if q else ""

    out.setdefault("thought", "")
    out.setdefault("reasoning", "")
    out["thought"] = str(out.get("thought") or "")
    out["reasoning"] = str(out.get("reasoning") or "")
    return out


@dataclass
class Decision:
    """The typed surface over the normalized dict — for NEW code paths.
    The graph still carries the dict; this object is the read contract."""

    action: Action = Action.USE_TOOL
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    question: str = ""
    thought: str = ""
    reasoning: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_model(cls, d: dict | None) -> "Decision":
        n = normalize_decision(d)
        try:
            action = Action(n["action"])
        except ValueError:
            action = n["action"]  # unknown-but-real (switch_skill et al.)
        return cls(
            action=action,
            tool_name=n["tool_name"],
            tool_args=n["tool_args"],
            question=n["question"],
            thought=n["thought"],
            reasoning=n["reasoning"],
            raw=n,
        )

    @property
    def is_ask(self) -> bool:
        return self.action in (Action.ASK_OPERATOR, Action.ASK_USER)
