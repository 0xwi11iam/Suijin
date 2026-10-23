"""
suijin/knowledge_graph.py
===========================
Persistent knowledge graph for verified exploitation constraints.

Every verified hypothesis, blocked pattern, confirmed CVE, and false-positive
finding is stored here. All attack branches consult this graph BEFORE generating
payloads — we never waste cycles on strings we already know get blocked.

Storage backends (v5.1):
  json   — the default: knowledge_graph.json. WORKSPACE-FIRST: the live
           graph is <workspace>/intel/knowledge_graph.json (learned
           intel NEVER writes into the package tree — that path once
           put a live engagement's API keys and target intel into git);
           the in-tree file is a READ-ONLY neutral seed, copied to the
           workspace on first boot.
  neo4j  — flip config.json "kg_backend": "neo4j" (+ neo4j_uri/user/
           password, or SUIJIN_NEO4J_* env vars) to switch. Same API,
           same result shapes; see kg_backend.py for the schema and the
           switch contract.
"""

import contextlib
import json
from pathlib import Path

from suijin.modules.redteam.lib.intel.kg_backend import get_backend

BASE_DIR = Path(__file__).resolve().parent
#: the READ-ONLY shipped seed: neutral, engagement-free lessons only.
#: The LIVE graph lives in the WORKSPACE — learned intel (findings,
#: API keys, target behavior) must NEVER write into the package tree:
#: that path put a live engagement's keys and target intel into git
#: (operator ruling 2026-10-23: nothing hardcoded, nothing engagement-
#: identifying in committed artifacts).
SEED_PATH = BASE_DIR / "knowledge_graph.json"


def _default_graph_path() -> Path:
    """Workspace-first resolution ( GRAPH_PATH stays the monkeypatch seam:
    tests reassign it and _kg() re-resolves on every call)."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        return WORKSPACE_DIR / "intel" / "knowledge_graph.json"
    return SEED_PATH


GRAPH_PATH = _default_graph_path()


# Back-compat file ops (tests and tooling use these directly on the JSON
# store; the neo4j backend is exercised through the public API below).
def _load():
    for p in (GRAPH_PATH, SEED_PATH):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


def _save(data):
    # workspace only — never the package seed
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _kg():
    """The configured backend. GRAPH_PATH is resolved at EVERY call so
    monkeypatching it (tests) keeps working through the seam."""
    return get_backend(_seeded_path)


def _seeded_path() -> Path:
    """GRAPH_PATH, materialized: on a fresh workspace the shipped seed
    is copied ONCE so the graph boots with its neutral lessons. A
    monkeypatched GRAPH_PATH (tests) is never touched — they own that
    file outright."""
    p = GRAPH_PATH
    if p == _default_graph_path() and SEED_PATH.exists() and not p.exists():
        with contextlib.suppress(Exception):
            import shutil

            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SEED_PATH, p)
    return p


# ---------------------------------------------------------------------------
# Public API — signatures unchanged; every function is backend-agnostic
# ---------------------------------------------------------------------------


def add_constraint(target, constraint_type, rule, evidence="", confidence=1.0):
    """Add a verified constraint to the knowledge graph.

    Args:
        target:          hostname or IP identifying the target system
        constraint_type: "blocks", "rate_limit", "waf", "verified_cve",
                         "false_positive", "behavior", "bypass"
        rule:            the constraint rule (e.g. "' OR 1=1")
        evidence:        what proved this constraint (response diff, status, etc.)
        confidence:      0.0–1.0, only 1.0 if binary-verified
    """
    _kg().add_constraint(target, constraint_type, rule, evidence=evidence, confidence=confidence)


def get_constraints(target):
    """Return all known constraints for a target.

    Shape (identical on every backend):
        {ctype: [{rule, evidence, confidence, verified_at, last_seen}, ...]}
    """
    return _kg().get_constraints(target)


def check_payload(target, payload):
    """Check if a payload matches any known blocked pattern.

    Returns dict: {blocked: bool, reason: str, confidence: float} or {'blocked': False}
    """
    constraints = get_constraints(target)
    blocks = constraints.get("blocks", [])
    payload_lower = payload.lower() if isinstance(payload, str) else ""

    for block in blocks:
        rule = block.get("rule", "")
        if not rule:
            continue
        rule_lower = rule.lower()
        if rule_lower in payload_lower:
            return {
                "blocked": True,
                "reason": f"Known block: '{rule}' (verified {block.get('verified_at', '?')})",
                "confidence": block.get("confidence", 1.0),
            }
    return {"blocked": False}


def check_cve(target, cve_id):
    """Check if a CVE has already been verified for this target."""
    constraints = get_constraints(target)
    verified = constraints.get("verified_cve", [])
    return any(c.get("rule") == cve_id for c in verified)


def get_bypass_strategies(target):
    """Return known working bypass strategies for this target."""
    return get_constraints(target).get("bypass", [])


def get_all_targets():
    """List all targets that have knowledge graph entries."""
    return _kg().get_all_targets()


def clear_target(target):
    """Remove all constraints for a target (for fresh recon)."""
    _kg().clear_target(target)


def summary(target):
    """Return a compact summary of what we know about a target."""
    constraints = get_constraints(target)
    if not constraints:
        return f"No knowledge recorded for {target}."

    lines = [f"Knowledge Graph • {target}"]
    for ctype, items in constraints.items():
        if ctype.startswith("_"):
            continue
        if not items:
            continue
        lines.append(f"  {ctype}:")
        for item in items:
            conf = f" [{item.get('confidence', 1.0):.0%}]" if item.get("confidence", 1.0) < 1.0 else ""
            lines.append(f"    • {item.get('rule', '?')}{conf}")
    return "\n".join(lines)


# ── G49: visualization export ──────────────────────────────────────────


def export_mermaid() -> str:
    """Whole-graph mermaid diagram (targets -> constraints).

    Reads through the backend (works on neo4j too): targets ->
    {ctype: [constraints]} rows, metadata keys skipped."""
    targets = get_all_targets()
    lines = ["graph LR"]
    for tname in targets:
        tdata = get_constraints(tname)
        node = "".join(c if c.isalnum() else "_" for c in tname)[:24]
        lines.append(f'  {node}["{tname[:20]}"]')
        for ctype, constraints in tdata.items():
            if ctype.startswith("_") or not isinstance(constraints, list):
                continue
            for c in constraints[:4]:  # cap edges per type
                if not isinstance(c, dict):
                    continue
                rule = str(c.get("rule", ""))[:28].replace('"', "'")
                edge = f"{node}_{ctype}_{abs(hash(rule)) % 997}"
                lines.append(f'  {node} -->|{ctype}| {edge}["{rule}"]')
    if len(lines) == 1:
        return "graph LR\n  empty[(knowledge graph is empty)]"
    return "\n".join(lines)
