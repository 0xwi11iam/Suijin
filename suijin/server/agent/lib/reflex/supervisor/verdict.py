"""Supervisor verdict pipeline — detection as classification, phrasing
as the only generation left.

THE BULLSHIT KILLER (operator: "supervisor is mostly bullshitting"):
the old flow asked a generator "identify problems" over a 10-step
snapshot — an RLHF model ALWAYS has an opinion, so healthy traces got
invented problems at 100% apparent certainty, and nothing could
measure whether any nudge helped.

This pipeline inverts the defaults:
  tier 0  rules (kept — free, deterministic, the loudest cases)
  tier 1  verdict classifier: {drift, stall, repeat, miss_chain, none}
          with ABSTAIN DOMINANT on healthy traces; borderline marks the
          timeline and needs next-turn confirmation
  tier 2  phrasing: ONE LLM call, only on a confirmed decision,
          grounded in the class + the features that fired — OFFER-shaped

When `decision.enabled` is false this module is never called; when the
classifier misses, the legacy coach path runs (the ladder).
"""

from __future__ import annotations

import contextlib

from suijin.modules.agent.lib.reflex.core.client import decision_config
from suijin.modules.agent.lib.reflex.core.features import build_features
from suijin.modules.agent.lib.reflex.core.questions import ABSTAIN, get_question
from suijin.modules.agent.lib.reflex.core.shadow import shadow_log


def reflex_enabled(config: dict | None) -> bool:
    return bool(decision_config(config).get("enabled"))


def verdict(state: dict, trace: list, config: dict | None) -> dict:
    """The tier-1 decision. Returns:
    {"act": bool, "kind": str, "p": float, "features": {...}, "raw": {...}}
    act=False means silence — the DOMINANT outcome on healthy traces."""
    q = get_question("supervisor.verdict")
    feats = build_features(state, trace)
    from suijin.modules.agent.lib.reflex.core.client import decide

    ans = decide("supervisor.verdict", feats, config)
    if ans is None or ans["choice"] == ABSTAIN:
        return {"act": False, "kind": "none", "p": ans["p"] if ans else 0.0, "features": feats, "raw": ans}
    choice, p = ans["choice"], ans["p"]
    if p < (q.threshold if q else 0.62):
        # borderline: mark the timeline, act only on repeat confirmation
        timeline = list((state or {}).get("_verdict_timeline") or [])
        timeline.append({"turn": feats["iteration"], "kind": choice, "p": p})
        if state is not None:  # write BACK — the next reading reads the history
            state["_verdict_timeline"] = timeline[-8:]
        confirmed = (
            len(timeline) >= 2
            and timeline[-1]["kind"] == timeline[-2]["kind"]
            and timeline[-1]["turn"] - timeline[-2]["turn"] <= 3
        )
        return {"act": confirmed, "kind": choice, "p": p, "features": feats, "raw": ans, "confirmed": confirmed}
    return {"act": True, "kind": choice, "p": p, "features": feats, "raw": ans}


# ── grounded phrasing: tier 2, the ONLY LLM left in the supervisor ──────

_GROUNDS = {
    "drift": "phase/tool mismatch: recon-shaped tools in {phase} for {phase_tool_mismatch} of the last steps",
    "stall": "no forward motion: {iterations_since_finding} iterations without a finding, {tool_fail_cluster} consecutive failures at the tail",
    "repeat": "repeat pressure {repeat_pressure}: near-identical calls crowding the trace",
    "miss_chain": "{chain_candidates} confirmed finding(s) sit unexploited while the trace moves elsewhere",
}


async def phrase(verdict_out: dict, generate_fn, state: dict) -> str | None:
    """ONE generation, grounded: the prompt carries the class + the
    features that fired, and demands an OFFER naming both. A generator
    handed the decision cannot invent the problem — only phrase it."""
    kind = verdict_out.get("kind")
    feats = verdict_out.get("features") or {}
    ground = _GROUNDS.get(kind, "")
    try:
        filled = ground.format(
            **{
                k: feats.get(k, "?")
                for k in (
                    "phase",
                    "phase_tool_mismatch",
                    "iterations_since_finding",
                    "tool_fail_cluster",
                    "repeat_pressure",
                    "chain_candidates",
                )
            }
        )
    except (KeyError, IndexError):
        filled = ground
    prompt = (
        "A deterministic classifier confirmed this engagement state:\n"
        f"VERDICT: {kind} (confidence {verdict_out.get('p', 0):.2f})\n"
        f"EVIDENCE: {filled}\n\n"
        "Write EXACTLY one line for the agent: an OFFER (not an order) that names "
        "the evidence and ONE concrete next action. No doctrine, no restating.\n"
        "OFFER: "
    )
    try:
        out = await generate_fn([{"role": "user", "content": prompt}], state.get("_run_config") or {})
        text = str(out or "").strip().splitlines()[0][:200]
        if not text or text.upper().startswith("NO_"):
            return None  # refuse rather than emit noise
        return text
    except Exception:  # noqa: BLE001 — phrasing failure = silence, not a crash
        return None


def shadow_supervisor(
    state: dict, trace: list, config: dict | None, classifier_out: dict, legacy: str | None, acted: bool
) -> None:
    """One shadow record per supervisor interval (cheap, outcome-closed later)."""
    with contextlib.suppress(Exception):
        feats = classifier_out.get("features") or build_features(state, trace)
        shadow_log(
            "supervisor.verdict",
            feats,
            classifier_out.get("raw"),
            legacy,
            acted,
            turn=int(feats.get("iteration") or 0),
        )
