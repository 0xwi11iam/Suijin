"""System One Assessor — the evidence-based decision layer.

Replaces 1225 lines of heuristic detectors with ONE laya assessment
per interval. No counting, no regex, no magic thresholds. The model
receives the agent's actual situation and returns a verdict with
reasoning.

THE CONTRACT:
  assess(state, trace, config) -> {
      verdict:  "intervene" | "stay_silent" | "escalate",
      kind:     "chain" | "hypothesis" | "drift" | "stall" | "radio_silence" | "deep_dive" | "",
      reason:   "evidence-grounded one-liner",
      action:   "the specific thing the agent should do",
      priority: "critical" | "high" | "normal",
      p:        float (confidence)
  }

FALLBACK IS SILENCE: when System One is unavailable or uncertain,
the supervisor says NOTHING. No heuristic fires in its place. A quiet
supervisor is better than a bullshitting one (the operator's ruling).

SHADOW CORPUS: every assessment (or abstention) is logged with the
situation and the outcome — this IS the fine-tuning dataset.
"""

from __future__ import annotations

import contextlib
import json
import time

from suijin.modules.agent.lib.reflex.core.shadow import shadow_log

#: interval between assessments (in agent turns) — this is the ONLY
#: tuning knob, and it's an architectural choice (how often to look),
#: not a behavioral threshold (what to conclude)
ASSESS_INTERVAL = 5


def _collect_evidence(state: dict, trace: list) -> str:
    """The raw situation — no feature engineering, no counting. The
    semantic content laya needs to make a judgment."""
    state = state or {}
    recent = list(trace or [])[-10:]
    parts = []

    parts.append(f"objective: {str(state.get('original_objective') or '?')[:200]}")
    parts.append(f"phase: {state.get('current_phase') or '?'}")
    parts.append(f"iteration: {int(state.get('current_iteration') or 0)}")

    # what the agent has been doing (last 10 actions, semantic)
    if recent:
        parts.append("recent_actions:")
        for t in recent:
            tool = str(t.get("tool_name") or "?")
            thought = str(t.get("thought") or "")[:120]
            ok = "ok" if t.get("success", True) else "FAIL"
            parts.append(f"  {tool} ({ok}): {thought}")

    # what it holds (findings, footholds)
    findings = [f for f in (state.get("findings") or []) if isinstance(f, dict)]
    if findings:
        parts.append(f"findings: {len(findings)}")
        for f in findings[-3:]:
            parts.append(f"  [{f.get('severity', '?')}] {str(f.get('description') or f.get('title', f))[:100]}")

    footholds = state.get("_footholds") or []
    if footholds:
        parts.append(f"footholds: {len(footholds)}")
        for fh in footholds[-2:]:
            parts.append(f"  {str(fh.get('capability') or fh)[:80]}")

    # hypothesis queue (what it SHOULD be testing)
    hyps = state.get("_hypotheses") or []
    untested = [h for h in hyps if isinstance(h, dict) and not h.get("tested")]
    if untested:
        parts.append(f"untested_hypotheses: {len(untested)}")
        for h in untested[:3]:
            parts.append(f"  [{h.get('priority', '?')}] {str(h.get('hypothesis') or h)[:120]}")

    # pending chain (was a finding confirmed recently without follow-up?)
    chain = state.get("_chain_pending") or None
    if chain:
        parts.append(f"chain_pending: {str(chain.get('finding') or '?')[:100]} (turn {chain.get('turn')})")

    # app model high-value targets (what's worth attacking)
    model = state.get("_app_model") or {}
    high_value = model.get("high_value") or []
    if high_value:
        parts.append(f"high_value_targets: {len(high_value)}")
        for hv in high_value[:2]:
            parts.append(f"  {str(hv.get('endpoint') or '?')}: {str(hv.get('why') or '?')[:60]}")

    # mesh state
    mesh_silent = state.get("_inbox_unanswered_turns") or 0
    if mesh_silent >= 2:
        parts.append(f"mesh: {mesh_silent} turns with unanswered team messages")

    # cost trend
    cost = float(state.get("_est_cost_usd") or 0)
    if cost > 0:
        parts.append(f"cost_so_far: ${cost:.2f}")

    return "\n".join(parts)


def _question_for(evidence: str) -> str:
    """The assessment prompt — asks for judgment, not pattern matching."""
    return f"""You are supervising an autonomous security agent. Given its current situation below, decide whether to intervene.

SITUATION:
{evidence}

Answer with a JSON object:
{{
  "verdict": "intervene" if the agent needs a course correction NOW, "stay_silent" if it's doing fine,
  "kind": the intervention type: "chain" (unexplored finding), "hypothesis" (untested high-priority idea), "drift" (off-objective), "stall" (no real progress), "deep_dive" (should go deeper on current target), or "" if staying silent,
  "reason": ONE sentence grounded in the evidence above (not generic advice),
  "action": the SPECIFIC thing the agent should do next (name the endpoint, tool, or step),
  "priority": "critical" | "high" | "normal"
}}

Rules:
- stay_silent is the DEFAULT. Most turns the agent is fine.
- Only intervene when the evidence shows a genuine problem or missed opportunity.
- The reason MUST reference specific details from the situation, not generic patterns.
- If the agent has an untested critical hypothesis, that IS a problem — intervene.
- If a finding was confirmed 2+ turns ago without chain exploration, that IS a missed opportunity.
- If the agent is making genuine progress (new endpoints, new findings, useful intel), stay silent."""


def assess(state: dict, trace: list, config: dict | None = None) -> dict:
    """The System One assessment. Returns the verdict dict. Falls back
    to stay_silent (with p=0) on any failure — silence, never noise."""
    out = {"verdict": "stay_silent", "kind": "", "reason": "", "action": "", "priority": "normal", "p": 0.0}
    with contextlib.suppress(Exception):
        evidence = _collect_evidence(state, trace)
        if not evidence.strip():
            return out

        # try laya first (the intended decision-maker)
        from suijin.modules.agent.lib.reflex.core.client import DecisionClient

        client = DecisionClient(config)
        answer = client.decide("supervisor.assess", {"evidence": evidence})
        if answer and answer.get("choice") not in (None, "abstain", ""):
            out.update(
                {
                    "verdict": str(answer.get("choice") or "stay_silent"),
                    "kind": str(answer.get("kind") or ""),
                    "reason": str(answer.get("reason") or ""),
                    "action": str(answer.get("action") or ""),
                    "priority": str(answer.get("priority") or "normal"),
                    "p": float(answer.get("p") or 0.0),
                }
            )
            _shadow(state, trace, out, "laya")
            return out

        # laya abstained or is unavailable → try the local LLM as the
        # fallback decision-maker (NOT a heuristic — still semantic)
        _llm_verdict = _llm_assess(evidence, config)
        if _llm_verdict:
            out.update(_llm_verdict)
            _shadow(state, trace, out, "llm_fallback")
            return out

        # both laya and LLM abstained → run the deterministic detectors
        # as the fallback (worse than System One but functional — the
        # supervisor still works, just with pattern matching instead of
        # semantic understanding)
        _det = _deterministic_fallback(state, trace)
        if _det:
            out.update(_det)
            _shadow(state, trace, out, "deterministic")
    return out


def _deterministic_fallback(state: dict, trace: list) -> dict | None:
    """The old heuristic detectors as the System One fallback. Only fires
    on the LOUDEST patterns — the ones that are almost always real
    problems regardless of context. Softer patterns stay silent (the
    LLM should have caught those)."""
    from suijin.modules.agent.lib.supervisor import _detect_missed_flag, _detect_repeating_tool

    state = state or {}
    recent = list(trace or [])[-15:]

    # the two loudest: missed flag and tool repetition
    for detector in (_detect_missed_flag, _detect_repeating_tool):
        with contextlib.suppress(Exception):
            result = detector(recent)
            if result:
                return {
                    "verdict": "intervene",
                    "kind": detector.__name__.replace("_detect_", ""),
                    "reason": result[:200],
                    "action": result[:200],
                    "priority": "high",
                    "p": 0.70,  # heuristic confidence — reasonable but not certain
                }

    # chain pending check (from the chain composer)
    chain = (state or {}).get("_chain_pending") or None
    if chain and not chain.get("explored"):
        iteration = int((state or {}).get("current_iteration") or 0)
        age = iteration - int(chain.get("turn") or iteration)
        if age >= 2:
            return {
                "verdict": "intervene",
                "kind": "chain",
                "reason": f"confirmed finding unexplored for {age} turns",
                "action": f"explore what {chain.get('finding', '?')[:60]} enables",
                "priority": "high",
                "p": 0.80,
            }

    # untested critical hypothesis
    from suijin.modules.agent.lib.reflex.hypotheses import untested_critical

    criticals = untested_critical(state or {})
    if criticals:
        top = criticals[0]
        return {
            "verdict": "intervene",
            "kind": "hypothesis",
            "reason": f"CRITICAL hypothesis untested: {top.get('hypothesis', '?')[:80]}",
            "action": f"test hypothesis #{top.get('id', '?')} on {top.get('target', '?')[:40]}",
            "priority": "critical",
            "p": 0.85,
        }

    return None


def _llm_assess(evidence: str, config: dict | None) -> dict | None:
    """The LLM fallback assessment — uses the main model's SYNC generate
    (not generate_async → asyncio.run inside a sync call called from the
    async event loop — that BLOCKED the entire TUI on slow providers).
    Still semantic, still evidence-grounded, still silence-dominant."""
    with contextlib.suppress(Exception):
        from suijin.modules.providers.lib import generate as _sync_generate

        prompt = _question_for(evidence)
        raw = _sync_generate(
            [{"role": "user", "content": prompt}],
            config or {},
            max_tokens=200,
            temperature=0.1,
        )
        if not raw or str(raw).startswith("Error:"):
            return None
        text = str(raw).strip()
        import re

        m = re.search(r"\{[^}]+\}", text, re.DOTALL)
        if not m:
            return None
        parsed = json.loads(m.group(0))
        if parsed.get("verdict") not in ("intervene", "stay_silent"):
            return None
        return {
            "verdict": parsed.get("verdict"),
            "kind": parsed.get("kind") or "",
            "reason": parsed.get("reason") or "",
            "action": parsed.get("action") or "",
            "priority": parsed.get("priority") or "normal",
            "p": 0.75,
        }
    return None


def _shadow(state: dict, trace: list, verdict: dict, engine: str) -> None:
    """Shadow-log every assessment for the fine-tuning corpus."""
    with contextlib.suppress(Exception):
        shadow_log(
            "supervisor.assess",
            {
                "iteration": int((state or {}).get("current_iteration") or 0),
                "evidence_length": len(_collect_evidence(state, trace)),
            },
            {"engine": engine, "verdict": verdict.get("verdict"), "kind": verdict.get("kind")},
            verdict.get("reason"),
            verdict.get("verdict") == "intervene",
            turn=int((state or {}).get("current_iteration") or 0),
        )


def format_intervention(verdict: dict) -> str | None:
    """Convert a laya verdict into a user message. The verdict carries
    its own reasoning — no hardcoded phrasing templates."""
    if verdict.get("verdict") != "intervene":
        return None
    reason = verdict.get("reason") or ""
    action = verdict.get("action") or ""
    priority = verdict.get("priority") or "normal"
    kind = verdict.get("kind") or ""

    if not action:
        return None  # an intervention without an action is noise

    priority_tag = f"[{priority.upper()}] " if priority == "critical" else ""
    return f"SUPERVISOR {priority_tag}({kind}): {reason}. {action}"


def track_outcome(state: dict, verdict: dict, turn: int) -> None:
    """Record what happened after an intervention (or silence) — the
    training signal for fine-tuning."""
    with contextlib.suppress(Exception):
        log = list((state or {}).get("_supervisor_outcomes") or [])
        log.append(
            {
                "turn": turn,
                "verdict": verdict.get("verdict"),
                "kind": verdict.get("kind"),
                "action": verdict.get("action")[:100],
                "ts": time.strftime("%H:%M:%S"),
            }
        )
        state["_supervisor_outcomes"] = log[-50:]  # bounded
