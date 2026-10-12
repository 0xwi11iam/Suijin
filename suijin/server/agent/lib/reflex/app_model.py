"""App Model — auto-populated from browser network capture + observations.

This IS the critical finder's foundation: a structured representation
of the target application that persists in state and feeds the
hypothesis generator. No manual curation — it fills from what the
agent observes.

THE CONTRACT:
  record_endpoint(state, method, path, auth, status)  — from net capture
  record_trust_boundary(state, frm, to, mechanism)     — from auth observations
  record_high_value(state, endpoint, why)              — from LLM assessment
  record_state_machine(state, name, states, race_risk) — from observation
  model_ready(state) -> bool                            — threshold for hypotheses
  render_model_summary(state) -> str                    — context injection
"""

from __future__ import annotations

import contextlib
import re


def _model(state: dict) -> dict:
    """Get or create the app model in state."""
    m = state.get("_app_model")
    if not isinstance(m, dict):
        m = {
            "stack": {},
            "endpoints": [],
            "trust_boundaries": [],
            "high_value": [],
            "state_machines": [],
            "data_flows": [],
        }
        state["_app_model"] = m
    return m


def _dedup(lst: list, key: str, entry: dict) -> list:
    """Add entry if not already present (keyed by the key field)."""
    existing = {str(e.get(key, "")) for e in lst if isinstance(e, dict)}
    if str(entry.get(key, "")) not in existing:
        lst.append(entry)
    return lst[:60]  # bounded


def record_endpoint(state: dict, method: str, path: str, auth: str = "", status: int = 0) -> None:
    """Record an endpoint discovered via network capture or probing."""
    with contextlib.suppress(Exception):
        m = _model(state)
        m["endpoints"] = _dedup(
            m["endpoints"],
            "path",
            {"method": str(method)[:8], "path": str(path)[:200], "auth": str(auth)[:40], "status": int(status or 0)},
        )


def record_trust_boundary(state: dict, frm: str, to: str, mechanism: str) -> None:
    """Record a trust boundary (e.g. anonymous → user via session cookie)."""
    with contextlib.suppress(Exception):
        m = _model(state)
        key = f"{frm}->{to}"
        m["trust_boundaries"] = _dedup(
            m["trust_boundaries"],
            "key",
            {"key": key, "from": str(frm)[:30], "to": str(to)[:30], "mechanism": str(mechanism)[:60]},
        )


def record_high_value(state: dict, endpoint: str, why: str) -> None:
    """Record a high-value target (money movement, PII, admin)."""
    with contextlib.suppress(Exception):
        m = _model(state)
        m["high_value"] = _dedup(m["high_value"], "endpoint", {"endpoint": str(endpoint)[:150], "why": str(why)[:100]})


def record_state_machine(state: dict, name: str, states: list, race_risk: str = "") -> None:
    """Record a state machine (order flow, payment, voting)."""
    with contextlib.suppress(Exception):
        m = _model(state)
        m["state_machines"] = _dedup(
            m["state_machines"],
            "name",
            {
                "name": str(name)[:50],
                "states": [str(s)[:20] for s in (states or [])[:6]],
                "race_risk": str(race_risk)[:60],
            },
        )


def record_from_network(state: dict, net_entries: list) -> None:
    """Auto-populate from browser network capture entries."""
    with contextlib.suppress(Exception):
        for e in net_entries:
            if not isinstance(e, dict) or e.get("type") != "request":
                continue
            url = str(e.get("url") or "")
            method = str(e.get("method") or "GET")
            # extract path from full URL
            m = re.search(r"https?://[^/]+(/[^?]*)", url)
            path = m.group(1) if m else url[:150]
            # detect auth from headers
            headers = e.get("headers") or {}
            auth = ""
            if headers.get("authorization") or headers.get("Authorization"):
                auth = "bearer"
            elif headers.get("cookie") or headers.get("Cookie"):
                auth = "cookie"
            record_endpoint(state, method, path, auth, 0)


def model_ready(state: dict) -> bool:
    """Has the model crossed the threshold for hypothesis generation?"""
    m = (state or {}).get("_app_model") or {}
    return len(m.get("endpoints") or []) >= 8 and len(m.get("trust_boundaries") or []) >= 1


def render_model_summary(state: dict) -> str:
    """The APP MODEL context section — what the agent knows about the target."""
    m = (state or {}).get("_app_model") or {}
    if not m.get("endpoints"):
        return ""
    lines = ["## APPLICATION MODEL (what you know about this target)"]

    endpoints = m.get("endpoints") or []
    lines.append(f"\nENDPOINTS ({len(endpoints)}):")
    for ep in endpoints[:15]:
        auth = f" [{ep['auth']}]" if ep.get("auth") else ""
        lines.append(f"  {ep.get('method', '?'):>4} {ep.get('path', '?')}{auth}")

    trust = m.get("trust_boundaries") or []
    if trust:
        lines.append(f"\nTRUST BOUNDARIES ({len(trust)}):")
        for tb in trust[:4]:
            lines.append(f"  {tb.get('from')} → {tb.get('to')} via {tb.get('mechanism')}")

    hv = m.get("high_value") or []
    if hv:
        lines.append(f"\nHIGH-VALUE TARGETS ({len(hv)}):")
        for h in hv[:4]:
            lines.append(f"  {h.get('endpoint')} — {h.get('why')}")

    sm = m.get("state_machines") or []
    if sm:
        lines.append(f"\nSTATE MACHINES ({len(sm)}):")
        for s in sm[:3]:
            states_str = " → ".join(s.get("states") or [])
            lines.append(f"  {s.get('name')}: {states_str} (race risk: {s.get('race_risk', '?')})")

    return "\n".join(lines)
