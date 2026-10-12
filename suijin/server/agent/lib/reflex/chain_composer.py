"""Chain Composer — post-finding enforcement.

When a finding is confirmed, the next turn gets a CHAIN DIRECTIVE.
If the agent doesn't explore what the finding enables within 2 turns,
the System One assessor escalates. Chain hypotheses auto-populate.

THE CONTRACT:
  on_finding_confirmed(state, finding)  — called by catalog/_on_confirmed
  check_chain_compliance(state, trace)   — called by think_node each turn
  render_chain_directive(state) -> str   — the context injection
"""

from __future__ import annotations

import contextlib


def on_finding_confirmed(state: dict, finding: dict) -> None:
    """Called when catalog_exploit CONFIRMS (or a finding reaches high
    confidence). Sets the chain-pending state and auto-populates chain
    hypotheses."""
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib.reflex.hypotheses import add_chain_hypotheses

        iteration = int((state or {}).get("current_iteration") or 0)
        state["_chain_pending"] = {
            "finding": str(finding.get("description") or finding.get("title") or finding)[:200],
            "endpoint": str(finding.get("endpoint") or "?")[:100],
            "severity": str(finding.get("severity") or "?")[:20],
            "turn": iteration,
            "explored": False,
        }
        add_chain_hypotheses(state, finding)


def check_chain_compliance(state: dict, trace: list) -> str | None:
    """Called by think_node: if a chain is pending and unexplored,
    return the escalation message. None = all clear."""
    pending = (state or {}).get("_chain_pending") or None
    if not pending or pending.get("explored"):
        return None

    iteration = int((state or {}).get("current_iteration") or 0)
    age = iteration - int(pending.get("turn") or iteration)
    if age < 1:
        return None  # just happened, give it a turn

    # check if the agent explored: did recent thoughts/tools reference
    # the finding or target a NEW endpoint it enables access to?
    finding_kw = str(pending.get("finding") or "").lower()[:60]
    endpoint_kw = str(pending.get("endpoint") or "").lower()[:40]
    recent = list(trace or [])[-3:]
    for t in recent:
        thought = str(t.get("thought") or "").lower()
        target = str(t.get("args") or "").lower()
        if finding_kw[:20] in thought or endpoint_kw[:20] in target:
            pending["explored"] = True
            state["_chain_pending"] = pending
            return None  # it IS exploring — good

    if age >= 2:
        # check inventory for the chain directive context
        return (
            f"CHAIN REQUIRED: you confirmed [{pending.get('severity')}] "
            f"{pending.get('finding', '?')[:100]} {age} turns ago and haven't explored "
            "what it enables. Your next tool call MUST test something this finding "
            "makes possible — an internal endpoint it reaches, a privilege it grants, "
            "data it exposes. This overrides your current plan."
        )
    elif age == 1:
        return (
            f"Chain reminder: [{pending.get('severity')}] {pending.get('finding', '?')[:80]} "
            "confirmed last turn — explore what it enables THIS turn."
        )
    return None


def render_chain_directive(state: dict) -> str:
    """The context section for pending chains — injected into the
    hypothesis/chain area of the context block."""
    pending = (state or {}).get("_chain_pending") or None
    if not pending or pending.get("explored"):
        return ""

    desc = pending.get("finding") or "?"
    sev = pending.get("severity") or "?"
    lines = [
        f"## CHAIN PENDING — [{sev}] finding unexplored",
        f"  finding: {desc[:150]}",
        f"  endpoint: {pending.get('endpoint', '?')}",
        "",
        "COMPOSE THE WORST 3-STEP CHAIN:",
        "  1. What does this finding ENABLE? (what can you reach now?)",
        "  2. What internal endpoint/service can you access with it?",
        "  3. What is the MAXIMUM impact — data, privilege, RCE?",
        "",
        "Your inventory (credentials, footholds, tokens) + this finding",
        "= an attack chain. Build it. Test it. Catalog the result.",
    ]
    return "\n".join(lines)


def clear_chain(state: dict) -> None:
    """Called when the chain has been explored or the finding archived."""
    with contextlib.suppress(Exception):
        state.pop("_chain_pending", None)
