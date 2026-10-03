"""Oracle reflex — triage and adjudication as classification.

The oracle today: regex anomalies on tool output (tier 0, kept) plus an
LLM hypothesis pass whose context ships the whole tool output — the
per-request wall-clock sink. Here:

  triage(qid oracle.triage) — anomaly CLASS in ms over response-shape
  features; regexes stay as tier-0; the classifier only gates whether
  the LLM hypothesis call fires AT ALL. Most outputs are `none` — most
  LLM calls die before they exist.

  adjudicate(qid oracle.adjudicate) — per-hypothesis
  {confirm, deny, need_more}; a DENY with high control-difference-free
  evidence feeds the cheatsheet as a dead battery (the two systems
  close the loop on each other).

Hypothesis GENERATION stays LLM — that is creative work.
"""

from __future__ import annotations

import contextlib

from suijin.modules.agent.lib.reflex.core.client import decide
from suijin.modules.agent.lib.reflex.core.features import adjudicate_features, triage_features
from suijin.modules.agent.lib.reflex.core.questions import ABSTAIN


def triage(tool_output: str, status_code: int = 200, elapsed_ms: float = 0.0, config: dict | None = None) -> dict:
    """Anomaly class for one tool response. `none` = no LLM hypothesis
    call should fire. Never raises."""
    with contextlib.suppress(Exception):
        feats = triage_features(tool_output, status_code, elapsed_ms)
        ans = decide("oracle.triage", feats, config)
        return {
            "class": (ans or {}).get("choice") or ABSTAIN,
            "p": float((ans or {}).get("p") or 0.0),
            "engine": (ans or {}).get("engine"),
        }
    return {"class": ABSTAIN, "p": 0.0, "engine": "miss"}


def adjudicate(hypothesis: dict, control_difference: float = 0.0, config: dict | None = None) -> dict:
    """Verdict for one hypothesis. `deny`+high-p is cheatsheet-worthy."""
    with contextlib.suppress(Exception):
        feats = adjudicate_features(hypothesis, control_difference)
        ans = decide("oracle.adjudicate", feats, config)
        out = {"verdict": (ans or {}).get("choice") or "need_more", "p": float((ans or {}).get("p") or 0.0)}
        if out["verdict"] == "deny" and out["p"] >= 0.7:
            _distill_dead_battery(hypothesis, feats)
        return out
    return {"verdict": "need_more", "p": 0.0}


def _distill_dead_battery(hypothesis: dict, feats: dict) -> None:
    """A high-confidence deny is transferable knowledge: record it in
    the cheatsheet (best-effort — the loop closes, never blocks)."""
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib import cheatsheet

        cheatsheet.add(
            f"hyp class {feats.get('hypothesis_class', '?')}: control delta "
            f"{feats.get('control_difference', 0)} and {feats.get('evidence_markers', 0)} "
            "markers — this shape did not reproduce; check conditions before retrying",
            tag="oracle-deny",
            source="oracle.adjudicate",
        )
