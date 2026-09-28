"""The drive state — the agent's wanting, as dynamics rather than labels.

Four dimensions, updated per turn from the epistemic state and trace:

  INTEREST   unresolved surprise energy (live surprises + high-value
             open questions) — the pull toward explaining things
  AMBITION   held-but-unused capability, aging — the pull toward USING
             what you won
  BOREDOM    a surface class yielding nothing new — the push AWAY from
             grinding
  URGENCY    open questions going stale — the pull toward closure

All decay 10%/turn without reinforcement, because real interest does.
Rendered as the agent's OWN condition — state descriptions, never
imperatives — and consumed by the controller (Layer 2) and the coach.

Pure, deterministic. State rides `state["_drive"]`.
"""

from __future__ import annotations

DECAY = 0.90
AMBITION_AGE_HORIZON = 6  # turns unused before ambition saturates
BOREDOM_STREAK = 3  # same-surface actions with nothing new before boredom


def blank() -> dict:
    return {"interest": 0.0, "ambition": 0.0, "boredom": 0.0, "urgency": 0.0, "_last_boring_surface": ""}


def ensure(state: dict) -> dict:
    d = state.get("_drive")
    if not isinstance(d, dict):
        d = blank()
    for k in ("interest", "ambition", "boredom", "urgency"):
        d.setdefault(k, 0.0)
    d.setdefault("_last_boring_surface", "")
    return d


def update(state: dict, epi: dict, iteration: int) -> dict:
    """One drive tick: recompute targets from the epistemic state, then
    blend with decay (momentum — drives don't teleport)."""
    import re

    from suijin.modules.agent.lib.epistemic import high_value_questions, live_surprises, open_questions
    from suijin.modules.agent.lib.footholds import unexploited

    d = ensure(state if "_drive" in state else {"_drive": blank()})

    # ── targets ─────────────────────────────────────────────────────
    surprises = live_surprises(epi)
    hq = high_value_questions(epi)
    t_interest = min(1.0, len(surprises) * 0.3 + len(hq) * 0.15)

    holds = unexploited(state.get("_footholds") or [])
    t_ambition = 0.0
    for f in holds:
        age = max(0, iteration - int(f.get("born_iter", iteration)))
        t_ambition = min(1.0, t_ambition + 0.3 * min(1.0, age / AMBITION_AGE_HORIZON))

    # boredom: consecutive same-surface actions with no new epistemic entries
    trace = state.get("execution_trace") or []
    recent = trace[-BOREDOM_STREAK:]
    surfaces = []
    for st in recent:
        m = re.search(r"https?://[^\s\"'<>]+", str(st.get("tool_args", "")))
        surfaces.append(_surface_key(m.group(0) if m else str(st.get("thought", ""))))
    same = len(set(surfaces)) == 1 and len(surfaces) == BOREDOM_STREAK and surfaces[0]
    grew = _epistemic_grew_recently(epi, iteration)
    if same and not grew:
        t_boredom = 1.0
        d["_last_boring_surface"] = surfaces[0]
    else:
        t_boredom = 0.0
        if not same:
            d["_last_boring_surface"] = ""

    stale = [q for q in open_questions(epi) if q.get("stale")]
    t_urgency = min(1.0, len(stale) * 0.35) if stale else 0.0

    # ── blend: 60% target + 40% momentum (with decay) ───────────────
    for key, target in (
        ("interest", t_interest),
        ("ambition", t_ambition),
        ("boredom", t_boredom),
        ("urgency", t_urgency),
    ):
        d[key] = round(min(1.0, 0.6 * float(target) + 0.4 * float(d[key]) * DECAY), 3)
    return d


def _surface_key(text: str) -> str:
    import re

    m = re.search(r"https?://[^/]+(/\S*)?", text or "")
    if m:
        return (m.group(1) or "/")[:40]
    return ""


def _epistemic_grew_recently(epi: dict, iteration: int) -> bool:
    """Any known/suspected/surprise entry born in the last 2 turns."""
    for s in epi.get("surprises", []):
        if iteration - int(s.get("last_seen", 0)) <= 2:
            return True
    for q in epi.get("open_questions", []):
        if iteration - int(q.get("last_touched", 99)) <= 2 and q.get("status") == "open":
            return True
    return False


def energy(d: dict) -> float:
    """Composite drive energy — the continuation criterion (Layer 2d)."""
    return round(min(1.0, float(d.get("interest", 0)) * 0.6 + float(d.get("ambition", 0)) * 0.4), 3)


def render(d: dict, epi: dict, iteration: int, state: dict | None = None) -> str:
    """The agent's own condition — state phrasing, zero imperatives,
    ≤4 lines, nonzero drives only. This is the model reading its own
    wanting the way it reads its board."""
    from suijin.modules.agent.lib.epistemic import live_surprises, open_questions

    rows = []
    surprises = live_surprises(epi)
    if d.get("interest", 0) >= 0.25 and surprises:
        s = surprises[-1]
        age = iteration - int(s.get("last_seen", iteration))
        rows.append(f"- UNEXPLAINED: {str(s.get('what', ''))[:90]} ({age} turn(s) unchased)")
    if d.get("ambition", 0) >= 0.3 and state:
        from suijin.modules.agent.lib.footholds import unexploited

        holds = unexploited(state.get("_footholds") or [])
        if holds:
            cap = str(holds[0].get("capability", ""))[:60]
            rows.append(f"- AMBITION: you hold '{cap}' unused — it unlocks something untouched")
    if d.get("boredom", 0) >= 0.8 and d.get("_last_boring_surface"):
        rows.append(f"- BORING: {d['_last_boring_surface'][:50]} has yielded nothing new for {BOREDOM_STREAK} actions")
    stale = [q for q in open_questions(epi) if q.get("stale") and q.get("status") == "open"]
    if d.get("urgency", 0) >= 0.3 and stale:
        rows.append(f'- STALE QUESTION: "{str(stale[0].get("question", ""))[:70]}" going unanswered')
    if not rows:
        return ""
    return "## DRIVE (your own state — what pulls at you)\n" + "\n".join(rows[:4]) + "\n"
