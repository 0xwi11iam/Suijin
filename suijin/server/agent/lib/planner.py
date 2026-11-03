"""The planner split (wall memo slice 4) — a second brain that cannot
run a command.

ARTEX's insight: 'one brain falls in love with the first route, treats
one filtered payload as "this is dead," and reports a version match as
a bug.' The planner emits intents; the worker (the main loop) claims
one and stops. The planner CANNOT dispatch — its only tools are
add_intent, prove_goal, steer, kill.

The empty-queue rule (an instruction, not a wall): if the goal is open
and nothing is running, it MUST emit ≥1 intent. Zero is legal only when
work is in flight or the goal is met.
"""

from __future__ import annotations

import contextlib
import json


def planner_system_prompt(state: dict) -> str:
    """The planner's contract — NO execution tools exist for it."""
    from suijin.modules.agent.lib.rows import open_intents, snapshot

    out = [
        "You are the PLANNER of an authorized bug bounty engagement. You "
        "do NOT execute anything — no tools, no commands, no requests. You "
        "read the rows and emit intents; the worker executes.",
        "",
        snapshot(),
        "",
        "YOUR OUTPUT: one JSON object, exactly:",
        '{"emit_intents": [{"text": "a verifiable RESULT (not a step)", "parent_fact": ""}], '
        '"prove_goal": {"goal": "...", "evidence_id": "...", "reason": "..."} or null, '
        '"note": "one line"}',
        "",
        "RULES:",
        "- If any goal is open and NO intent is running: you MUST emit at least 1 "
        "intent. An empty queue with an open goal is a bug.",
        "- An intent names a RESULT: 'exfiltrate the admin token via the "
        "imgres chain', NOT 'run nmap'.",
        "- prove_goal only when the measured evidence closes it — class "
        "weights AND asset coverage; a thin negative is not closure.",
        f"- Currently open: {len(open_intents())} intents.",
    ]
    return "\n".join(out)


def parse_plan(raw: str) -> dict:
    """Parse the planner's JSON (tolerant — normalize_decision for the
    planner shape). Returns {intents: [...], prove: {...}|None}."""
    import re

    from suijin.server.agent.lib.decision import normalize_decision

    with contextlib.suppress(Exception):
        m = re.search(r"\{.*\}", str(raw or ""), re.S)
        if m:
            d = normalize_decision(json.loads(m.group(0)))
            return {
                "intents": d.get("emit_intents") or [],
                "prove": d.get("prove_goal") or None,
                "note": str(d.get("note") or "")[:200],
            }
    return {"intents": [], "prove": None, "note": ""}


def apply_plan(plan: dict) -> str:
    """Write the plan's intents into the rows; prove_goal is returned as
    the completion directive for the main loop to verify."""
    from suijin.modules.agent.lib.rows import add_intent

    added = []
    for i in plan.get("intents") or []:
        t = str((i or {}).get("text") or "").strip()
        if t:
            added.append(add_intent(t, str((i or {}).get("parent_fact") or "")))
    out = f"planner: {len(added)} intent(s) added" if added else "planner: no intents"
    if plan.get("prove"):
        out += " · PROVE GOAL: " + json.dumps(plan["prove"])[:200]
    return out


def should_wake_planner(state: dict) -> bool:
    """Fires when a worker finishes, a finding lands, or the queue is
    empty with an open goal (the empty-queue rule — must emit)."""
    from suijin.modules.agent.lib.rows import open_intents

    oi = open_intents()
    if not oi:
        return True  # empty queue: the planner MUST look
    return any(i["state"] == "running" for i in oi) is False and len(oi) < 3
