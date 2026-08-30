"""Blue actor dossier — per-actor intelligence from every blue artifact.

Merges: case records (CaseStore) + KG attacker history + enforcement
state (blocks/canary hits) + traffic volume. Renders markdown.
"""

from __future__ import annotations


def build_dossier(ip: str, case_store, kg, enforcement_snapshot: dict | None = None) -> dict:
    """Per-actor dossier merging every blue source."""
    dossier = {"ip": ip, "cases": {}, "kg": {}, "enforcement": {}, "richness": 0}

    try:
        summary = case_store.actor_summary(ip)
        dossier["cases"] = summary
        dossier["richness"] += summary.get("total_cases", 0)
    except Exception:
        pass

    try:
        kg_hist = kg.get_attacker_history(ip)
        dossier["kg"] = {
            "total_flags": kg_hist.get("total_flags", 0),
            "attacks": len(kg_hist.get("attacks", [])),
            "defenses": len(kg_hist.get("defenses", [])),
            "first_seen": kg_hist.get("attacker", {}).get("first_seen", ""),
        }
        dossier["richness"] += kg_hist.get("total_flags", 0)
    except Exception:
        pass

    try:
        if enforcement_snapshot:
            dossier["enforcement"] = {
                "blocked": ip in (enforcement_snapshot.get("blocks") or {}),
                "redirected": ip in (enforcement_snapshot.get("redirects") or {}),
                "canary_hits": sum(1 for h in (enforcement_snapshot.get("canary_hits") or []) if h.get("ip") == ip),
            }
            dossier["richness"] += dossier["enforcement"]["canary_hits"]
            if dossier["enforcement"]["blocked"]:
                dossier["richness"] += 1
    except Exception:
        pass

    return dossier


def render_dossier(d: dict) -> str:
    """Markdown dossier with intel richness score."""
    ip = d.get("ip", "?")
    lines = [f"# Dossier — {ip}", ""]

    cases = d.get("cases", {})
    if cases.get("total_cases"):
        lines.append(f"## Cases ({cases['total_cases']} total, {cases.get('open_cases', 0)} open)")
        lines.append(f"- max severity: {cases.get('max_severity', 0)}/10")
        if cases.get("attack_types"):
            at = ", ".join(f"{k}({v})" for k, v in sorted(cases["attack_types"].items()))
            lines.append(f"- attack types: {at}")
        if cases.get("mitre_techniques"):
            lines.append(f"- ATT&CK: {', '.join(cases['mitre_techniques'])}")
        if cases.get("endpoints"):
            lines.append(f"- endpoints ({len(cases['endpoints'])}): {', '.join(cases['endpoints'][:8])}")
        lines.append("")

    kg = d.get("kg", {})
    if kg.get("total_flags"):
        lines.append(f"## Knowledge Graph ({kg['total_flags']} flags)")
        lines.append(f"- attacks recorded: {kg.get('attacks', 0)}")
        lines.append(f"- defenses applied: {kg.get('defenses', 0)}")
        if kg.get("first_seen"):
            lines.append(f"- first seen: {kg['first_seen']}")
        lines.append("")

    enf = d.get("enforcement", {})
    if enf:
        lines.append("## Enforcement State")
        if enf.get("blocked"):
            lines.append("- status: **BLOCKED**")
        if enf.get("redirected"):
            lines.append("- status: REDIRECTED")
        if enf.get("canary_hits"):
            lines.append(f"- canary trips: {enf['canary_hits']}")
        lines.append("")

    lines.append(f"*intel richness: {d.get('richness', 0)} item(s)*")
    return "\n".join(lines)
