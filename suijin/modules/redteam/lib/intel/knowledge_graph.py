"""
suijin/knowledge_graph.py
===========================
Persistent knowledge graph for verified exploitation constraints.

Every verified hypothesis, blocked pattern, confirmed CVE, and false-positive
finding is stored here. All attack branches consult this graph BEFORE generating
payloads — we never waste cycles on strings we already know get blocked.

Storage: JSON file at suijin/knowledge_graph.json (human-readable, git-friendly)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
GRAPH_PATH = BASE_DIR / "knowledge_graph.json"


def _load():
    if not GRAPH_PATH.exists():
        return {}
    try:
        return json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data):
    GRAPH_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
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
    data = _load()
    entry = data.setdefault(target, {})
    category = entry.setdefault(constraint_type, [])

    # Deduplicate
    existing = [c for c in category if c.get("rule") == rule]
    if existing:
        existing[0]["evidence"] = evidence
        existing[0]["confidence"] = max(existing[0].get("confidence", 0), confidence)
        existing[0]["last_seen"] = datetime.now(timezone.utc).isoformat()
    else:
        category.append(
            {
                "rule": rule,
                "evidence": evidence,
                "confidence": confidence,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }
        )

    # Update global metadata
    entry["_updated"] = datetime.now(timezone.utc).isoformat()
    _save(data)


def get_constraints(target):
    """Return all known constraints for a target."""
    data = _load()
    return data.get(target, {})


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
    constraints = get_constraints(target)
    return constraints.get("bypass", [])


def get_all_targets():
    """List all targets that have knowledge graph entries."""
    data = _load()
    return [k for k in data if not k.startswith("_")]


def clear_target(target):
    """Remove all constraints for a target (for fresh recon)."""
    data = _load()
    data.pop(target, None)
    _save(data)


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
    """Whole-graph mermaid diagram (targets -> constraints)."""
    data = _load()
    lines = ["graph LR"]
    targets = data.get("targets") or {}
    for tname, tdata in targets.items():
        node = "".join(c if c.isalnum() else "_" for c in tname)[:24]
        lines.append(f'  {node}["{tname[:20]}"]')
        for c in tdata.get("constraints") or []:
            ctype = c.get("type", "?")
            rule = str(c.get("rule", ""))[:28].replace('"', "'")
            lines.append(f'  {node} -->|{ctype}| {node}_{ctype}_{abs(hash(rule)) % 997}["{rule}"]')
    if len(lines) == 1:
        return "graph LR\n  empty[(knowledge graph is empty)]"
    return "\n".join(lines)
