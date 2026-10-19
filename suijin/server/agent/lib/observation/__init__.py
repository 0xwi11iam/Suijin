"""Observation engine — deterministic IDOR/authz/mass-assignment detection.

The CyberStrike insight: every HTTP request already carries the evidence.
Accumulate (endpoint, method, credential, params, response shape) per call,
then diff deterministically — no LLM needed to SEE that user_a and user_b
got different responses for the same object. The model still decides what
to TEST; the system surfaces what the data already SHOWS.

Three detectors (all pure functions, independently testable):
- IDOR: same endpoint + different credentials + different response = access control gap
- BFLA (vertical): endpoint reached by one credential class but not another
- Mass-assignment: same endpoint, one credential sends extra params that stick
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Observation:
    endpoint: str  # normalized: method + path (no query string)
    method: str
    credential: str  # named credential, "anon", or ""
    params: dict = field(default_factory=dict)
    status: int = 0
    resp_size: int = 0
    resp_location: str = ""
    iter: int = 0


@dataclass
class Finding:
    kind: str  # idor | bfla | mass_assignment
    endpoint: str
    evidence: str
    severity: str  # high | medium | info


# ── accumulation (called from the tool layer after every HTTP call) ─────

_OBSERVATIONS: list[Observation] = []
_MAX_OBS = 5000


def record(
    endpoint: str,
    method: str,
    credential: str,
    params: dict,
    status: int,
    resp_size: int,
    resp_location: str = "",
    iteration: int = 0,
) -> None:
    """Record one HTTP observation. Never raises; capped."""
    if len(_OBSERVATIONS) >= _MAX_OBS:
        _OBSERVATIONS.pop(0)
    _OBSERVATIONS.append(
        Observation(
            endpoint=_normalize(endpoint),
            method=str(method or "GET").upper(),
            credential=str(credential or "anon"),
            params=dict(params or {}),
            status=int(status or 0),
            resp_size=int(resp_size or 0),
            resp_location=str(resp_location or "")[:200],
            iter=int(iteration or 0),
        )
    )


def _normalize(endpoint: str) -> str:
    """Strip query strings and normalize path IDs to :id placeholders."""
    path = str(endpoint or "").split("?")[0]
    path = re.sub(r"/\d{3,}(?=/|$)", "/:id", path)
    path = re.sub(r"/[0-9a-f]{8,}-?[0-9a-f]*", "/:uuid", path, flags=re.I)
    return path or "/"


def reset() -> None:
    """Reset accumulated observations. Called by the test conftest and
    at engagement start — the observation engine's state is per-run."""
    _OBSERVATIONS.clear()


def observations() -> list[Observation]:
    return list(_OBSERVATIONS)


def _is_test_env() -> bool:
    """True when running under pytest — the observation engine stays
    quiet in tests (recording pollutes unrelated tool tests)."""
    import sys

    return "pytest" in sys.modules


# ── detectors (pure — no state mutation, testable in isolation) ────────


def detect_idor(min_size_diff: int = 200) -> list[Finding]:
    """Same endpoint, different credentials, materially different responses."""
    by_ep: dict[str, dict[str, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    for o in _OBSERVATIONS:
        if o.method in ("GET", "DELETE") and o.status > 0:
            by_ep[f"{o.method} {o.endpoint}"][o.credential].append(o)

    out: list[Finding] = []
    for ep_key, by_cred in by_ep.items():
        creds = {c: obs for c, obs in by_cred.items() if obs}
        if len(creds) < 2:
            continue
        statuses = {c: obs[0].status for c, obs in creds.items()}
        sizes = {c: max(o.resp_size for o in obs) for c, obs in creds.items()}
        # one cred gets 200, another gets 403/404 → access control gap
        status_set = set(statuses.values())
        if len(status_set) > 1:
            ok_creds = [c for c, s in statuses.items() if s < 300]
            denied = [c for c, s in statuses.items() if s >= 400]
            if ok_creds and denied:
                out.append(
                    Finding(
                        kind="idor",
                        endpoint=ep_key,
                        evidence=f"{ok_creds[0]} gets {statuses[ok_creds[0]]} ({sizes[ok_creds[0]]}B), "
                        f"{denied[0]} gets {statuses[denied[0]]} — different objects or access levels",
                        severity="high",
                    )
                )
        # both 200 but materially different sizes → different data returned
        elif len(set(sizes.values())) > 1:
            largest = max(sizes, key=sizes.get)
            smallest = min(sizes, key=sizes.get)
            if sizes[largest] - sizes[smallest] >= min_size_diff:
                out.append(
                    Finding(
                        kind="idor",
                        endpoint=ep_key,
                        evidence=f"{largest} sees {sizes[largest]}B, {smallest} sees {sizes[smallest]}B "
                        f"(diff {sizes[largest] - sizes[smallest]}B) — different data per credential",
                        severity="medium",
                    )
                )
    return out


def detect_bfla() -> list[Finding]:
    """Vertical privilege: endpoint reached by privileged cred but not by others."""
    by_ep: dict[str, dict[str, int]] = defaultdict(dict)
    for o in _OBSERVATIONS:
        if o.status > 0:
            prev = by_ep[f"{o.method} {o.endpoint}"].get(o.credential, 999)
            by_ep[f"{o.method} {o.endpoint}"][o.credential] = min(prev, o.status)

    out: list[Finding] = []
    for ep_key, statuses in by_ep.items():
        if len(statuses) < 2:
            continue
        ok = [c for c, s in statuses.items() if s < 300]
        denied = [c for c, s in statuses.items() if s >= 400]
        # if only ONE credential can reach it and others can't → BFLA candidate
        if len(ok) == 1 and len(denied) >= 1:
            out.append(
                Finding(
                    kind="bfla",
                    endpoint=ep_key,
                    evidence=f"only {ok[0]} reaches this ({statuses[ok[0]]}); "
                    f"{', '.join(denied[:3])} get {statuses[denied[0]]} — vertical access control",
                    severity="high",
                )
            )
    return out


def detect_mass_assignment() -> list[Finding]:
    """Same endpoint, one credential sends extra params that the response acknowledges."""
    by_ep: dict[str, dict[str, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    for o in _OBSERVATIONS:
        if o.method in ("POST", "PUT", "PATCH") and o.params:
            by_ep[f"{o.method} {o.endpoint}"][o.credential].append(o)

    out: list[Finding] = []
    for ep_key, by_cred in by_ep.items():
        all_params: set[str] = set()
        for obs_list in by_cred.values():
            for o in obs_list:
                all_params.update(o.params.keys())
        base_params: set[str] = set()
        for obs_list in by_cred.values():
            for o in obs_list:
                base_params = base_params or set(o.params.keys())
        extra = all_params - base_params
        if extra and len(by_cred) >= 1:
            out.append(
                Finding(
                    kind="mass_assignment",
                    endpoint=ep_key,
                    evidence=f"params {sorted(extra)} sent beyond the base set {sorted(base_params)} — "
                    "verify these are allowlisted server-side",
                    severity="info",
                )
            )
    return out


def detect_all() -> list[Finding]:
    """All detectors — the observation engine's public surface."""
    return detect_idor() + detect_bfla() + detect_mass_assignment()


def render_for_context(max_items: int = 5) -> str:
    """Deterministic findings for the think context — surfaces what the
    accumulated data already shows. Empty when no patterns detected."""
    findings = detect_all()
    if not findings:
        return ""
    lines = ["## OBSERVATION ENGINE (deterministic — what your data already shows)", ""]
    for f in findings[:max_items]:
        lines.append(f"- [{f.severity.upper()}] {f.kind.upper()} on {f.endpoint}: {f.evidence}")
    if len(findings) > max_items:
        lines.append(f"- (+{len(findings) - max_items} more — check with observation_status)")
    return "\n".join(lines)


def status() -> str:
    """observation_status tool: summary of accumulated evidence + findings."""
    obs_count = len(_OBSERVATIONS)
    findings = detect_all()
    eps = len({f"{o.method} {o.endpoint}" for o in _OBSERVATIONS})
    creds = len({o.credential for o in _OBSERVATIONS})
    out = [
        f"Observation engine: {obs_count} requests across {eps} endpoints, {creds} credentials.",
    ]
    if findings:
        out.append(f"\n{len(findings)} deterministic findings:")
        for f in findings[:10]:
            out.append(f"  [{f.severity.upper()}] {f.kind} {f.endpoint}: {f.evidence}")
    else:
        out.append("No deterministic findings yet — keep testing with different credentials.")
    return "\n".join(out)
