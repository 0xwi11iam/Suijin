"""Dispatch tools for the rows (wall-memo slice 2d).

The MODEL writes rows too — not just the harness. record_fact on intent
conclusion, insert_asset on discovery, add_intent from the plan. One
fact per intent, not one per curl.
"""

from __future__ import annotations


def rows_record_fact(summary: str = "", evidence: str = "", asset_id: str = "", observed: bool = True, confidence: float = 0.8) -> str:
    from suijin.modules.agent.lib.rows import record_fact

    if not str(summary or "").strip():
        return "Error: summary required (one line, the conclusion — not one per request)"
    fid = record_fact(str(summary), str(evidence or ""), str(asset_id or ""), observed, confidence)
    return f"fact {fid} recorded — it survives compaction; next turn's snapshot carries it"


def rows_insert_asset(name: str = "", atype: str = "", parent: str = "") -> str:
    from suijin.modules.agent.lib.rows import insert_asset

    if not str(name or "").strip() or not str(atype or "").strip():
        return "Error: name and type required (root_domain|subdomain|ip|service|app|endpoint)"
    aid = insert_asset(str(name), str(atype), str(parent or ""))
    return f"asset {aid} ({atype}:{name}) — idempotent: same name returns the same id"


def rows_add_intent(text: str = "", parent_fact: str = "") -> str:
    from suijin.modules.agent.lib.rows import add_intent

    if not str(text or "").strip():
        return "Error: text required — a verifiable RESULT, not a step (recon/scanning/'verify the bug' are not intents)"
    iid = add_intent(str(text), str(parent_fact or ""))
    return f"intent {iid} open — the planner dispatches it; claim it and work it to done|exhausted|blocked"


def rows_set_intent(iid: str = "", state: str = "", note: str = "") -> str:
    from suijin.modules.agent.lib.rows import INTENT_STATES, set_intent_state

    if not iid or state not in INTENT_STATES:
        return f"Error: iid + state required, one of {INTENT_STATES} (exhausted=budget cut it, continue later; blocked=transient, redispatch; done=do not retry)"
    ok = set_intent_state(iid, state, note)
    return f"intent {iid} → {state}" if ok else f"intent {iid} not found"


def rows_snapshot_tool() -> str:
    from suijin.modules.agent.lib.rows import snapshot

    return snapshot()
