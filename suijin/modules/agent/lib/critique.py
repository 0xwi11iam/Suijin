"""Self-critique — the agent reviews its own engagement and writes learnings.

After an engagement completes, an LLM pass over the execution trace
produces structured critique: what worked, what wasted calls, missed
leads, and concrete tactics worth remembering. Learnings land in:

  - the red knowledge graph (record_finding, type=self_critique) so
    future engagements consult them via check_knowledge
  - outputs/reports/critique_<thread>.md for the operator

Config-gated (config.json: "self_critique": true, default on when an
LLM is configured). Never raises into the caller — critique failure is
logged, not fatal.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("suijin.critique")

_CRITIQUE_PROMPT = """You are reviewing your OWN completed security engagement.
Objective: {objective}
Result: {reason} ({ok}/{total} steps succeeded, ${{cost}} spent)

Execution trace (tool, args-digest, outcome, duration):
{trace}

Write a brutally honest self-critique as JSON:
{{
  "what_worked": ["..."],
  "what_wasted": ["calls/time spent on dead ends — be specific"],
  "missed_leads": ["signals in the outputs you should have followed"],
  "tactics_to_remember": ["concrete, reusable tactics for this target type"],
  "verdict": "one sentence overall grade of your own performance"
}}
JSON only."""


def _format_trace(trace: list, limit: int = 40) -> str:
    lines = []
    for step in (trace or [])[-limit:]:
        tool = step.get("tool_name") or step.get("tool") or "?"
        args = step.get("tool_args") or step.get("args") or {}
        keys = ",".join(sorted(args)) if isinstance(args, dict) else str(args)[:40]
        ok = "ok" if step.get("success", True) else "FAIL"
        ms = step.get("duration_ms") or 0
        lines.append(f"- {tool}({keys}) {ok} {ms}ms")
    return "\n".join(lines) or "(empty trace)"


def run_self_critique(
    *,
    objective: str,
    final_state: dict,
    config: dict,
    generate_fn=None,
    thread_id: str = "default",
) -> dict | None:
    """Run the critique pass. Returns the parsed critique dict or None.

    generate_fn: async callable(messages, config) -> str. Defaults to the
    providers' generate (resolved lazily).
    """
    if not config.get("self_critique", True):
        logger.info("self-critique disabled by config")
        return None
    trace = final_state.get("execution_trace") or []
    if not trace:
        logger.info("no trace to critique")
        return None

    if generate_fn is None:
        from suijin.modules.redteam.lib.red.llm_client import generate_async

        generate_fn = generate_async

    prompt = _CRITIQUE_PROMPT.format(
        objective=objective[:200],
        reason=final_state.get("completion_reason", "?"),
        ok=sum(1 for s in trace if s.get("success", True)),
        total=len(trace),
        cost=final_state.get("cost_usd", 0),
        trace=_format_trace(trace),
    )
    try:
        import asyncio

        raw = asyncio.run(generate_fn([{"role": "user", "content": prompt}], config))
    except Exception as e:  # noqa: BLE001 — critique is never fatal
        logger.warning("self-critique LLM call failed: %s", e)
        return None
    if not isinstance(raw, str) or raw.startswith("Error"):
        logger.warning("self-critique got provider error: %s", str(raw)[:120])
        return None

    # the JSON has no 'action' key — parse permissively, not via decision parser
    try:
        start, end = raw.find("{"), raw.rfind("}")
        critique = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        logger.warning("self-critique output unparseable")
        return None
    if not isinstance(critique, dict) or not critique:
        return None

    _write_report(critique, objective, thread_id, final_state)
    _record_learnings(critique, config, final_state)
    return critique


def _write_report(critique: dict, objective: str, thread_id: str, final_state: dict) -> None:
    try:
        from suijin.modules.platform.lib.workspace import artifact_dir

        rdir = artifact_dir("reports")
        rdir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in thread_id if c.isalnum() or c in "-_")[:40] or "engagement"
        path = rdir / f"critique_{safe}.md"
        lines = [
            f"# Self-critique — {objective[:120]}",
            f"_completion: {final_state.get('completion_reason', '?')}, "
            f"{sum(1 for s in (final_state.get('execution_trace') or []) if s.get('success', True))}"
            f"/{len(final_state.get('execution_trace') or [])} steps_",
            "",
        ]
        for section, title in (
            ("what_worked", "What worked"),
            ("what_wasted", "What wasted effort"),
            ("missed_leads", "Missed leads"),
            ("tactics_to_remember", "Tactics to remember"),
        ):
            items = critique.get(section) or []
            if items:
                lines.append(f"## {title}")
                lines += [f"- {i}" for i in items[:10]]
                lines.append("")
        if critique.get("verdict"):
            lines.append(f"> {critique['verdict']}")
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("critique report at %s", path)
    except Exception:  # noqa: BLE001
        logger.warning("critique report write failed", exc_info=True)


def _record_learnings(critique: dict, config: dict, final_state: dict) -> None:
    """Tactics worth remembering go into the KG for future check_knowledge.

    Recorded via add_constraint directly (type='behavior' — the general
    bucket; the shared record_finding type enum is not extended) with a
    [self-critique] prefix for searchability.
    """
    try:
        from suijin.modules.redteam.lib.intel import knowledge_graph as kg

        target = (final_state.get("target_info") or {}).get("name") or "methodology"
        for tactic in (critique.get("tactics_to_remember") or [])[:5]:
            kg.add_constraint(
                target,
                "behavior",
                f"[self-critique] {str(tactic)[:190]}",
                evidence="agent self-critique pass",
                confidence=0.8,  # heuristic advice, not binary-verified
            )
    except Exception:  # noqa: BLE001
        logger.warning("critique KG recording failed", exc_info=True)
