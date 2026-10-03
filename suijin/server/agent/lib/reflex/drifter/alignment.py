"""Drifter — objective alignment promoted to a first-class concept.

Drift was one regex inside the supervisor's bag, sampled at intervals.
With a 10ms-class classifier it becomes what it should have been: a
PER-TURN alignment reading between the objective and the recent action
mix, kept as a TIMELINE so interventions see trajectory (three turns
drifting) instead of a single-turn snapshot (which false-positives).

The timeline rides state as `_drift` and exports `_drift_score` (0..1)
for supervisor.verdict's feature digest — drift feeds the supervisor,
it does not get its own voice (one intervention budget, tagged origin).
"""

from __future__ import annotations

import contextlib

from suijin.modules.agent.lib.reflex.core.client import decide
from suijin.modules.agent.lib.reflex.core.features import build_features
from suijin.modules.agent.lib.reflex.core.questions import ABSTAIN


def _score_of(kind: str) -> float:
    return {"on_course": 0.0, ABSTAIN: 0.2, "drifting": 0.6, "off_course": 1.0}.get(kind, 0.2)


def align(state: dict, trace: list, config: dict | None) -> dict:
    """One per-turn alignment reading. Returns the updated timeline head
    and writes `_drift`/`_drift_score` into state (same seam guidance
    uses). Never raises — the drifter may never break a turn."""
    out = {"kind": ABSTAIN, "p": 0.0, "sustained": False, "score": 0.2}
    with contextlib.suppress(Exception):
        feats = build_features(state or {}, trace)
        ans = decide("drifter.alignment", feats, config)
        kind = (ans or {}).get("choice") or ABSTAIN
        p = float((ans or {}).get("p") or 0.0)
        timeline = list((state or {}).get("_drift") or [])[-9:]
        timeline.append({"turn": feats.get("iteration"), "kind": kind, "p": p})
        # SUSTAINED drift: 3 of the last 4 readings at drifting-or-worse —
        # the trajectory gate that kills single-turn false positives
        tail = [t["kind"] for t in timeline[-4:]]
        sustained = sum(1 for k in tail if k in ("drifting", "off_course")) >= 3
        score = max(_score_of(k) for k in tail) if tail else 0.2
        if state is not None:
            state["_drift"] = timeline
            state["_drift_score"] = score
        out = {"kind": kind, "p": p, "sustained": sustained, "score": score}
    return out
