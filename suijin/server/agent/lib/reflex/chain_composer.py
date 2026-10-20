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

# THE HEARTBEAT HANDOFF (2026-10-17): _on_confirmed runs in the tools
# layer with NO graph access — it used to call on_finding_confirmed()
# with a THROWAWAY dict, so _chain_pending was set on garbage and the
# CHAIN directive never rendered once in a 202-iteration run. The
# pending chain lands here (module-level); think_node drains it into
# graph state on the next turn.
_PENDING_HANDOFF: dict | None = None


def on_finding_confirmed(state: dict, finding: dict) -> None:
    """Called when catalog_exploit CONFIRMS (or a finding reaches high
    confidence). Sets the chain-pending state and auto-populates chain
    hypotheses.

    F3 (2026-10-20): the chain hypotheses used to be written ONLY onto
    the throwaway tools-layer dict (add_chain_hypotheses(state, ...))
    — they died there. They now ride the module handoff alongside the
    chain record and are merged into graph state at drain time."""
    global _PENDING_HANDOFF
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib.reflex.hypotheses import add_chain_hypotheses

        iteration = int((state or {}).get("current_iteration") or 0)
        rec = {
            "finding": str(finding.get("description") or finding.get("title") or finding)[:200],
            "endpoint": str(finding.get("endpoint") or "?")[:100],
            "severity": str(finding.get("severity") or "?")[:20],
            "turn": iteration,
            "explored": False,
        }
        state["_chain_pending"] = rec
        handoff = dict(rec)
        # F3: capture the hypotheses onto a dict the drain can read —
        # add_chain_hypotheses writes state["_hypotheses"], so give it
        # the handoff itself
        add_chain_hypotheses(handoff, finding)
        _PENDING_HANDOFF = handoff


def drain_pending(state: dict) -> dict | None:
    """think_node calls this each turn: any confirmed finding that
    landed in the tools layer becomes graph state. Returns the record
    (or None) — the caller merges it into its updates.

    F3: the handoff may carry `_hypotheses` (chain hypotheses born at
    confirm time) — merge them into graph state here, they previously
    died on the throwaway dict."""
    global _PENDING_HANDOFF
    if _PENDING_HANDOFF is None:
        return state.get("_chain_pending")
    rec = _PENDING_HANDOFF
    _PENDING_HANDOFF = None
    hyps = rec.pop("_hypotheses", None)
    if hyps:
        with contextlib.suppress(Exception):
            existing = list(state.get("_hypotheses") or [])
            known = {str(h.get("hypothesis") or h.get("id")) for h in existing}
            for h in hyps:
                if str(h.get("hypothesis") or h.get("id")) not in known:
                    existing.append(h)
            state["_hypotheses"] = existing
    existing = state.get("_chain_pending") or {}
    if existing.get("explored"):
        return existing  # an older chain is mid-exploration — keep it
    return rec


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
    # F2 (2026-10-20): the trace stores tool_args (think_node :1416) —
    # this read `args` only, so the actions channel NEVER matched and
    # only a thought mentioning the finding could clear a chain.
    finding_kw = str(pending.get("finding") or "").lower()[:60]
    endpoint_kw = str(pending.get("endpoint") or "").lower()[:40]
    recent = list(trace or [])[-3:]
    for t in recent:
        thought = str(t.get("thought") or "").lower()
        target = str(t.get("tool_args") or t.get("args") or "").lower()
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


def render_chain_directive(state: dict, iteration_now: int = 0) -> str:
    """The context section for pending chains — injected into the
    hypothesis/chain area of the context block."""
    pending = drain_pending(state)
    if not pending or pending.get("explored"):
        return ""

    desc = pending.get("finding") or "?"
    sev = pending.get("severity") or "?"
    stale = int(iteration_now or 0) - int(pending.get("turn") or 0)
    urgent = stale >= 2
    head = (
        f"## ⚠ CHAIN REQUIRED — [{sev}] CONFIRMED finding sits UNEXPLOITED"
        if urgent
        else f"## CHAIN PENDING — [{sev}] finding unexplored"
    )
    lines = [
        head,
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
    if urgent:
        lines.append(
            "\nThis is not optional: chaining confirmed findings IS the job. "
            "You will not complete while this sits unexplored."
        )
    return "\n".join(lines)


def clear_chain(state: dict) -> None:
    """Called when the chain has been explored or the finding archived."""
    with contextlib.suppress(Exception):
        state.pop("_chain_pending", None)
