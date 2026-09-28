"""The epistemic state — the agent's model of what it knows and,
critically, what it DOESN'T.

The drive controller's sensory layer. Three tiers:
  KNOWN         verified facts (with evidence refs)
  SUSPECTED     signals, partial evidence
  OPEN QUESTIONS each with an information-value estimate — the ignorance
                map, where curiosity actually lives

SURPRISE is the currency: every observation is scanned for prediction
errors (diff verdicts, oracle hypotheses, status deltas, anomaly
language), and each live surprise spawns an open question. Leak-named
entities become reachability questions; held capabilities become unlock
questions. Questions age, resolve to findings, or die to dead ends.

Pure, deterministic, zero LLM calls. State rides `state["_epistemic"]`.
"""

from __future__ import annotations

import re

MAX_OPEN = 8
MAX_SURPRISES = 10
STALE_TURNS = 12

# ── surprise patterns (deterministic output scanning) ────────────────────

_SURPRISE_PATTERNS = [
    (re.compile(r"DIFF DETECTED|VERDICT[: ] +(MEDIUM|HIGH|SIGNIFICANT)", re.I), "response diff"),
    (re.compile(r"\[H[123]\]", re.I), "oracle hypothesis"),
    (re.compile(r"anomal|unexpected|inconsistent|behaves differently", re.I), "anomaly language"),
    (re.compile(r"surpris|contradict|should not (be|have|return)", re.I), "contradiction"),
]

_LEAK_RE = re.compile(
    r"\b(?:internal|private|hidden|secret|backend|upstream)\s+(?:service|api|host|endpoint|server)?\s*"
    r"[:=-]?\s*([a-z][a-z0-9-]{2,30}(?:\.[a-z0-9-]{2,10}){0,3})\b",
    re.I,
)
_ENTITY_RE = re.compile(r"\b((?:svc|service|api|internal|admin|graphql|gql)-[a-z0-9-]{2,20})\b", re.I)
#: connective words the leak pattern may skip — never captured themselves
_STOPWORDS = {
    "service",
    "services",
    "referenced",
    "mentioned",
    "internal",
    "api",
    "host",
    "endpoint",
    "server",
    "config",
    "the",
    "named",
    "called",
}

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _surface_of(text: str) -> str:
    """The surface a piece of text is about (first URL path or 'general')."""
    m = _URL_RE.search(text or "")
    if not m:
        return "general"
    url = m.group(0)
    return re.sub(r"^https?://[^/]+", "", url)[:60] or "/"


# ── the state ────────────────────────────────────────────────────────────


def blank() -> dict:
    return {"known": {}, "suspected": {}, "open_questions": [], "surprises": [], "_qid": 0}


def ensure(state: dict) -> dict:
    epi = state.get("_epistemic")
    if not isinstance(epi, dict):
        epi = blank()
    for k, v in (
        ("known", {}),
        ("suspected", {}),
        ("open_questions", []),
        ("surprises", []),
        ("_qid", 0),
    ):
        epi.setdefault(k, v)
    return epi


# ── per-observation update ───────────────────────────────────────────────


def observe(state: dict, tool_name: str, output: str, iteration: int) -> dict:
    """Fold one tool result into the belief state. Never raises.

    Called once per execute result; everything is derived from the
    observation text plus existing state — deterministic, cheap, and
    the only place surprise enters the system."""
    epi = ensure(state if "_epistemic" in state else {"_epistemic": blank()})
    out = str(output or "")
    surface = _surface_of(out) or "general"

    _detect_surprise(epi, out, surface, iteration)
    if tool_name in ("record_finding", "catalog_exploit") or "CONFIRMED" in out[:200]:
        _fold_confirmed(epi, out, surface, iteration)
    _extract_leak_questions(epi, out, iteration)
    _age_and_resolve(epi, iteration)
    epi["open_questions"] = epi["open_questions"][-MAX_OPEN:]
    epi["surprises"] = epi["surprises"][-MAX_SURPRISES:]
    return epi


def _detect_surprise(epi: dict, out: str, surface: str, iteration: int) -> None:
    for rx, kind in _SURPRISE_PATTERNS:
        m = rx.search(out)
        if not m:
            continue
        what = out[max(0, m.start() - 60) : m.end() + 120].replace("\n", " ").strip()[:180]
        # dedup by rough content overlap on the same surface
        for s in epi["surprises"]:
            if s.get("surface") == surface and s.get("status") == "live" and _similar(s.get("what", ""), what):
                s["last_seen"] = iteration
                return
        epi["surprises"].append(
            {
                "iter": iteration,
                "last_seen": iteration,
                "what": what,
                "surface": surface,
                "kind": kind,
                "status": "live",
            }
        )
        _add_question(
            epi,
            f"explain the {kind} at {surface}",
            expected_info="high",
            source="surprise",
            iteration=iteration,
        )
        return  # one surprise per observation


def _similar(a: str, b: str) -> bool:
    wa, wb = set(re.findall(r"[a-z]{4,}", a.lower())), set(re.findall(r"[a-z]{4,}", b.lower()))
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) > 0.7


def _fold_confirmed(epi: dict, out: str, surface: str, iteration: int) -> None:
    """A CONFIRMED lands in KNOWN and resolves overlapping questions."""
    head = next((ln for ln in out.splitlines() if "CONFIRMED" in ln), out[:160]).strip()
    epi["known"].setdefault(surface, []).append(head[:200])
    # resolve questions whose surface/question overlaps
    for q in epi["open_questions"]:
        if q.get("status") != "open":
            continue
        if _similar(q.get("question", ""), head) or surface in q.get("question", ""):
            q["status"] = "resolved"
            q["resolution"] = head[:120]
    # surprise on this surface is explained
    for s in epi["surprises"]:
        if s.get("surface") == surface and s.get("status") == "live":
            s["status"] = "explained"


def _extract_leak_questions(epi: dict, out: str, iteration: int) -> None:
    """Names leaked in evidence become reachability questions — the
    generative chaining leap ('internal service named svc-x → does it
    answer anywhere?')."""
    blob = out[:4000]
    names = set()
    for rx in (_LEAK_RE, _ENTITY_RE):
        for m in rx.finditer(blob):
            name = m.group(1).lower().strip(".")
            if name not in _STOPWORDS:
                names.add(name)
    for n in sorted(names)[:3]:  # cap per observation
        if len(n) < 4 or n in ("admin", "api", "internal"):
            continue
        _add_question(
            epi,
            f"is '{n}' (leaked name) reachable or resolvable anywhere in scope?",
            expected_info="med",
            source="leak",
            iteration=iteration,
        )


def capability_questions(state: dict, epi: dict, iteration: int) -> None:
    """Held-but-unused footholds become unlock questions (called from the
    think wiring where footholds are fresh)."""
    from suijin.modules.agent.lib.footholds import unexploited

    for f in unexploited(state.get("_footholds") or []):
        for t in (f.get("unlock_targets") or [])[:2]:
            _add_question(
                epi,
                f"can the held capability '{str(f.get('capability', ''))[:60]}' reach {str(t)[:60]}?",
                expected_info="high",
                source="capability",
                iteration=iteration,
            )


def _add_question(epi: dict, question: str, *, expected_info: str, source: str, iteration: int) -> None:
    q_norm = re.sub(r"[^a-z0-9 ]", "", question.lower()).strip()
    for q in epi["open_questions"]:
        qn = re.sub(r"[^a-z0-9 ]", "", str(q.get("question", "")).lower()).strip()
        if qn == q_norm or _similar(qn, q_norm):
            q["last_touched"] = iteration
            return  # dedup
    epi["_qid"] += 1
    epi["open_questions"].append(
        {
            "id": f"q{epi['_qid']}",
            "question": question[:200],
            "expected_info": expected_info,
            "source": source,
            "born": iteration,
            "last_touched": iteration,
            "status": "open",
            "resolution": None,
        }
    )


def _age_and_resolve(epi: dict, iteration: int) -> None:
    for q in epi["open_questions"]:
        if q.get("status") == "open" and iteration - int(q.get("last_touched", 0)) > STALE_TURNS:
            q["stale"] = True
        else:
            q["stale"] = False
    for s in epi["surprises"]:
        if s.get("status") == "live" and iteration - int(s.get("last_seen", 0)) > STALE_TURNS:
            s["status"] = "cold"


def mark_dead(epi: dict, surface: str, iteration: int) -> None:
    """A dead-end verdict on a surface kills its questions (called by the
    wiring when surface_verdict/DEAD END lands)."""
    for q in epi["open_questions"]:
        if q.get("status") == "open" and surface in str(q.get("question", "")):
            q["status"] = "dead"
            q["resolution"] = "dead end"


# ── queries for the drive ────────────────────────────────────────────────


def live_surprises(epi: dict) -> list[dict]:
    return [s for s in epi.get("surprises", []) if s.get("status") == "live"]


def open_questions(epi: dict) -> list[dict]:
    return [q for q in epi.get("open_questions", []) if q.get("status") == "open"]


def high_value_questions(epi: dict) -> list[dict]:
    return [q for q in open_questions(epi) if q.get("expected_info") == "high"]


def unfinished(state: dict) -> list[dict]:
    """Open + dead questions — the cross-session 'unfinished business' a
    completed engagement flushes to scratchpad and the next one inherits."""
    epi = ensure(state)
    return [q for q in epi.get("open_questions", []) if q.get("status") in ("open", "dead")]


def inherit(state: dict, questions: list[dict], iteration: int = 0) -> None:
    """Carry another engagement's unfinished business in as inherited
    open questions (turn-1 scratchpad re-orientation path)."""
    epi = ensure(state)
    for q in questions or []:
        _add_question(
            epi,
            str(q.get("question", ""))[:200],
            expected_info=str(q.get("expected_info") or "med"),
            source="inherited",
            iteration=iteration,
        )
    state["_epistemic"] = epi
