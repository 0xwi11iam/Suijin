"""The Foothold Graph (R7) — depth as a state machine, not doctrine prose.

Field complaint: "it finds something, doesn't go any further — digs like
a scavenger hunt." Structural cause: chain memory was MODEL-VOLUNTEERED
(an optional JSON field nobody fills), the breadth engine was
state-enforced (_attack_queue + the untried-surfaces completion gate),
and holdings vanished into flat artifacts. The fix: capabilities the
agent holds are extracted at CONFIRM time, ride every turn's context as
a YOU HOLD section, block completion while their unlocks are untested,
and close via the todo mechanism.

A foothold: {capability, source, unlock_targets: [...], born_iter, status}
status: "open" (created) → "armed" (unlocks named) → "tested" (todo done).
"""

from __future__ import annotations

import re

_CONF_RE = re.compile(r"(EXP-\d+)\s+CONFIRMED[^\n]*?[—-]\s*([^\n]{0,140})")
_MAX_FOOTHOLDS = 24
_MAX_UNLOCKS = 3


def extract_footholds(output: str, iteration: int) -> list[dict]:
    """Deterministic extraction from a CONFIRMED result line.

    No LLM here — the capability is the confirmed finding itself. The
    UNLOCK targets are named by a bounded follow-up call (arm_foothold)
    or by the model's next decision; extraction itself must never fail,
    never cost a token."""
    out = []
    for m in _CONF_RE.finditer(str(output or "")):
        eid, title = m.group(1), m.group(2).strip(" :.")
        if not title:
            title = eid
        out.append(
            {
                "capability": f"{eid}: {title[:120]}",
                "source": eid,
                "unlock_targets": [],
                "born_iter": int(iteration or 0),
                "status": "open",
            }
        )
    return out[:_MAX_UNLOCKS]


async def arm_foothold(foothold: dict, finding_head: str, generate_fn) -> list[str]:
    """One bounded LLM call: name what this finding UNLOCKS.

    'What does this unlock?' is the chaining question — asking it AT
    confirm time (instead of hoping the model asks itself later) is the
    whole mechanism. Bounded: 3 lines max, no stream, any failure just
    leaves the foothold open (the YOU HOLD section still asks)."""
    import contextlib

    prompt = (
        "You are assisting a penetration tester. A finding was just CONFIRMED:\n"
        f"{str(finding_head)[:600]}\n\n"
        "Name up to 3 CONCRETE next surfaces or actions this capability unlocks "
        "(endpoints reachable, sessions it grants, trust it bypasses). One short line "
        "each, no numbering, no prose. If it unlocks nothing actionable, reply NONE."
    )
    with contextlib.suppress(Exception):
        kw = {}
        try:
            import inspect

            params = inspect.signature(generate_fn).parameters
            if "on_delta" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
                kw["on_delta"] = False
        except (TypeError, ValueError):
            pass
        resp = await generate_fn([{"role": "user", "content": prompt}], {}, **kw)
        lines = [ln.strip("-• ").strip() for ln in str(resp or "").splitlines() if ln.strip()]
        lines = [ln for ln in lines if ln and ln.upper() != "NONE"][:_MAX_UNLOCKS]
        if lines:
            foothold["unlock_targets"] = lines
            foothold["status"] = "armed"
            return lines
    return []


def merge_footholds(state: dict, found: list[dict]) -> list[dict]:
    """Dedup by source EXP id, cap the list, newest last (age shows in context)."""
    existing = list(state.get("_footholds") or [])
    have = {f.get("source") for f in existing}
    for f in found:
        if f.get("source") not in have:
            existing.append(f)
            have.add(f.get("source"))
    return existing[-_MAX_FOOTHOLDS:]


def unexploited(footholds: list, iteration: int | None = None) -> list[dict]:
    """Footholds whose unlock targets remain untested — the depth-debt list.

    A foothold counts as exploited once its status is 'tested'; aging is
    surfaced (not filtered) so the context flags how long capability has
    been sitting unused."""
    out = []
    for f in footholds or []:
        if f.get("status") == "tested":
            continue
        if not f.get("unlock_targets"):
            out.append(f)  # open + unnamed still counts: name it or drop it
            continue
        out.append(f)
    return out


def render_you_hold(footholds: list, iteration: int = 0) -> str:
    """The YOU HOLD context section — the arsenal on the table, every turn.

    This is the attention mechanism that makes chaining locally optimal:
    the model sees its unused capabilities with ages attached, right next
    to the worklist. Scavenging becomes visibly wasteful."""
    rows = []
    for f in footholds or []:
        age = max(0, int(iteration or 0) - int(f.get("born_iter") or 0))
        status = f.get("status", "open")
        cap = str(f.get("capability", "?"))
        targets = f.get("unlock_targets") or []
        if status == "tested":
            continue
        flag = (
            (
                " ⚠ name what this unlocks: add todos id fh-%s-1.. with task 'test unlock: …', complete them as tested"
                % f.get("source", "?")
            )
            if status == "open"
            else ""
        )
        stale = f" · {age} turns old" if age >= 3 else ""
        rows.append(f"- HOLD: {cap}{stale}{flag}")
        for t in targets:
            rows.append(f"    → UNTESTED UNLOCK: {str(t)[:140]}")
    if not rows:
        return ""
    return (
        "## YOU HOLD (capabilities won — use them before hunting more)\n"
        + "\n".join(rows)
        + "\nClose a holding via its todo (todo_updates: status done) once tested.\n"
    )


def foothold_todos(footholds: list) -> list[dict]:
    """Todos for named unlocks — the FOLLOW-UP RULE made mechanical."""
    todos = []
    for f in footholds or []:
        if f.get("status") == "armed":
            for i, t in enumerate(f.get("unlock_targets") or []):
                todos.append(
                    {"id": f"fh-{f.get('source', '?')}-{i}", "task": f"test unlock: {str(t)[:120]}", "status": "open"}
                )
    return todos


def close_from_todo(footholds: list, todo_id: str) -> list[dict]:
    """A completed fh-* todo closes its foothold (tested)."""
    out = list(footholds or [])
    for f in out:
        if str(todo_id).startswith(f"fh-{f.get('source', '?')}-"):
            f["status"] = "tested"
    return out
