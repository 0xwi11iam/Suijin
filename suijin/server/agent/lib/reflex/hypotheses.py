"""Hypothesis queue — the critical finder's spine.

Generated from the application model, prioritized by impact, enforced
by the completion gate. This is what transforms the agent from a
surface scanner into an adversary: it has specific ideas to test,
not just payloads to fire.

THE CONTRACT:
  generate_from_model(state, config) -> list[dict]  (LLM-driven)
  untested_critical(state) -> list[dict]            (gate check)
  mark_tested(state, hypothesis_id, result)          (machine-verified)
  can_complete(state) -> (bool, str)                 (gate refusal)
"""

from __future__ import annotations

import contextlib
import json
import re


def generate_from_model(state: dict, config: dict | None = None) -> list[dict]:
    """Generate attack hypotheses from the application model. LLM-driven
    — the creative leap from 'what exists' to 'what would break it'."""
    model = (state or {}).get("_app_model") or {}
    endpoints = model.get("endpoints") or []
    trust = model.get("trust_boundaries") or []
    high_value = model.get("high_value") or []
    state_machines = model.get("state_machines") or []

    if not endpoints:
        return []

    with contextlib.suppress(Exception):
        import asyncio

        from suijin.modules.redteam.lib.red.llm_client import generate_async

        evidence = []
        for ep in endpoints[:15]:
            evidence.append(f"  {ep.get('method', '?')} {ep.get('path', '?')} (auth: {ep.get('auth', '?')})")
        for tb in trust[:3]:
            evidence.append(f"  trust: {tb.get('from')} → {tb.get('to')} via {tb.get('mechanism')}")
        for hv in high_value[:5]:
            evidence.append(f"  high-value: {hv.get('endpoint')} — {hv.get('why')}")
        for sm in state_machines[:3]:
            evidence.append(f"  state machine: {sm.get('name')} ({sm.get('race_risk', 'unknown race risk')})")

        prompt = f"""You are an adversarial security researcher. Given this application model, generate 5 SPECIFIC attack hypotheses.

APPLICATION MODEL:
{chr(10).join(evidence)}

A hypothesis is NOT "test for XSS" — it's a specific, testable claim about how to BREAK this application.

Format each as JSON:
{{"hypothesis": "<one sentence naming the endpoint, the assumption violated, and the impact>", "target": "<endpoint>", "class": "<idor|race|auth_bypass|ssrf|injection|logic|chain>", "priority": "<critical|high|medium>"}}

Rules:
- Name the specific endpoint and the specific assumption
- "critical" = data access, privilege escalation, or financial impact
- "high" = significant security control bypass
- Think like an ATTACKER: what would you DO if you wanted to steal data or escalate?
- Chain hypotheses are valid: "use X to access Y"

Respond with ONLY a JSON array of 5 objects."""

        raw = asyncio.run(
            generate_async(
                [{"role": "user", "content": prompt}],
                config or {},
                {"max_tokens_per_request": 600, "temperature": 0.3},
            )
        )
        if not raw:
            return []

        # parse the JSON array
        text = str(raw).strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        arr = json.loads(m.group(0))
        out = []
        for i, h in enumerate(arr[:8]):
            if isinstance(h, dict) and h.get("hypothesis"):
                out.append(
                    {
                        "id": i + 1,
                        "hypothesis": str(h.get("hypothesis"))[:200],
                        "target": str(h.get("target") or "?")[:100],
                        "class": str(h.get("class") or "logic")[:30],
                        "priority": str(h.get("priority") or "medium")[:20],
                        "tested": False,
                        "result": "",
                    }
                )
        return out
    return []


def should_generate(state: dict) -> bool:
    """Has the app model crossed the threshold for hypothesis generation?"""
    model = (state or {}).get("_app_model") or {}
    endpoints = model.get("endpoints") or []
    trust = model.get("trust_boundaries") or []
    existing = (state or {}).get("_hypotheses") or []
    return len(endpoints) >= 8 and len(trust) >= 1 and not existing


def untested_critical(state: dict) -> list[dict]:
    """Critical-priority hypotheses that haven't been tested."""
    return [
        h
        for h in ((state or {}).get("_hypotheses") or [])
        if isinstance(h, dict) and not h.get("tested") and str(h.get("priority")).lower() == "critical"
    ]


def untested_high(state: dict) -> list[dict]:
    return [
        h
        for h in ((state or {}).get("_hypotheses") or [])
        if isinstance(h, dict) and not h.get("tested") and str(h.get("priority")).lower() == "high"
    ]


def mark_tested(state: dict, hyp_id: int, result: str) -> None:
    """Machine-verified: the hypothesis was actually probed."""
    with contextlib.suppress(Exception):
        for h in state.get("_hypotheses") or []:
            if isinstance(h, dict) and h.get("id") == hyp_id:
                h["tested"] = True
                h["result"] = str(result)[:200]


def can_complete(state: dict) -> tuple[bool, str]:
    """The completion gate check: refuse to end while critical
    hypotheses are untested. Returns (ok, reason_if_not)."""
    criticals = untested_critical(state)
    if criticals:
        names = "; ".join(h.get("hypothesis", "?")[:60] for h in criticals[:3])
        return False, f"{len(criticals)} untested CRITICAL hypotheses: {names}"
    return True, ""


def render_for_context(state: dict) -> str:
    """The HYPOTHESES context section — what the agent should be testing."""
    hyps = (state or {}).get("_hypotheses") or []
    if not hyps:
        return ""
    lines = []
    untested = [h for h in hyps if isinstance(h, dict) and not h.get("tested")]
    tested = [h for h in hyps if isinstance(h, dict) and h.get("tested")]
    if untested:
        lines.append("UNTESTED (test these BEFORE new surface exploration):")
        for h in untested[:5]:
            pr = h.get("priority", "?")
            lines.append(f"  [{pr.upper():>8}] #{h.get('id', '?')} {h.get('hypothesis', '?')}")
    if tested:
        lines.append(f"\nTESTED ({len(tested)}):")
        for h in tested[-3:]:
            lines.append(f"  #{h.get('id', '?')} → {h.get('result', '?')[:60]}")
    return "\n".join(lines)


def add_chain_hypotheses(state: dict, finding: dict) -> None:
    """After a confirmed finding, auto-populate chain-derived hypotheses."""
    with contextlib.suppress(Exception):
        hyps = state.setdefault("_hypotheses", [])
        next_id = max((h.get("id", 0) for h in hyps if isinstance(h, dict)), default=0) + 1
        desc = str(finding.get("description") or finding.get("title") or finding)[:100]
        # chain hypotheses: what does this finding enable?
        hyps.append(
            {
                "id": next_id,
                "hypothesis": f"CHAIN: {desc} — what internal endpoints can this reach?",
                "target": str(finding.get("endpoint") or "?")[:100],
                "class": "chain",
                "priority": "critical" if str(finding.get("severity")).lower() in ("critical", "high") else "high",
                "tested": False,
                "result": "",
            }
        )
        hyps.append(
            {
                "id": next_id + 1,
                "hypothesis": f"CHAIN: {desc} — can this be escalated to data access or RCE?",
                "target": str(finding.get("endpoint") or "?")[:100],
                "class": "chain",
                "priority": "high",
                "tested": False,
                "result": "",
            }
        )
