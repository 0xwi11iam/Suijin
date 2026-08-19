"""Report templates (C19) + multi-language rendering (C20).

One finding set, three audiences:
  exec      — one page: risk posture, top risks, business language
  technical — full detail: evidence, reproduction, remediation
  compliance— mapping view: CWE/OWASP/ATT&CK columns
Language presets adjust section headers + canned phrases (en/de/fr/es).
"""

from __future__ import annotations

_LANG = {
    "en": {
        "title": "Security Assessment Report",
        "summary": "Executive Summary",
        "risks": "Top Risks",
        "detail": "Technical Detail",
        "remediation": "Remediation",
        "posture": "Overall risk posture",
        "none": "No findings recorded.",
    },
    "de": {
        "title": "Sicherheitsbewertungsbericht",
        "summary": "Zusammenfassung",
        "risks": "Top-Risiken",
        "detail": "Technische Details",
        "remediation": "Empfehlungen",
        "posture": "Gesamtrisikolage",
        "none": "Keine Feststellungen.",
    },
    "fr": {
        "title": "Rapport d'evaluation de securite",
        "summary": "Resume executif",
        "risks": "Risques principaux",
        "detail": "Detail technique",
        "remediation": "Remediation",
        "posture": "Posture de risque globale",
        "none": "Aucune constatation.",
    },
    "es": {
        "title": "Informe de evaluacion de seguridad",
        "summary": "Resumen ejecutivo",
        "risks": "Riesgos principales",
        "detail": "Detalle tecnico",
        "remediation": "Remediacion",
        "posture": "Postura de riesgo general",
        "none": "Sin hallazgos.",
    },
}


def render(
    findings: list,
    engagement: str = "engagement",
    template: str = "exec",
    language: str = "en",
    meta: dict | None = None,
) -> str:
    lang = _LANG.get((language or "en").lower(), _LANG["en"])
    meta = meta or {}
    findings = findings or []
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ordered = sorted(findings, key=lambda f: sev_rank.get(str(f.get("severity", "low")).lower(), 9))
    critical = [f for f in ordered if str(f.get("severity", "")).lower() in ("critical", "high")]

    head = [f"# {lang['title']} — {engagement}", ""]
    if template == "exec":
        if not findings:
            return "\n".join(head + [lang["none"]])
        head += [f"## {lang['summary']}", ""]
        head.append(
            f"{lang['posture']}: "
            + ("HIGH RISK — immediate action required" if critical else "moderate — schedule remediation")
        )
        head += ["", f"## {lang['risks']}", ""]
        for f in critical[:5]:
            head.append(
                f"- **{str(f.get('severity', '?')).upper()}** {f.get('type', '?')} — {str(f.get('evidence', ''))[:80]}"
            )
        head += [
            "",
            f"_{len(findings)} findings total ({len(critical)} high/critical). {lang['remediation']}: see technical report._",
        ]
        return "\n".join(head)

    if template == "technical":
        head += [f"## {lang['detail']}", ""]
        if not findings:
            return "\n".join(head + [lang["none"]])
        for i, f in enumerate(ordered, 1):
            v = (f.get("verification") or {}).get("verdict", "-")
            pr = (f.get("peer_review") or {}).get("verdict", "-")
            head += [
                f"### {i}. [{str(f.get('severity', '?')).upper()}] {f.get('type', '?')} — {f.get('target', f.get('endpoint', '?'))}",
                f"- confidence: {f.get('confidence', 'probable')} | verification: {v} | peer: {pr}",
                f"- evidence: {str(f.get('evidence', ''))[:300]}",
                f"- {lang['remediation']}: {str(meta.get('remediation_hint', 'patch / harden per vendor guidance'))}",
                "",
            ]
        return "\n".join(head)

    if template == "compliance":
        head += ["## Compliance Mapping", ""]
        if not findings:
            return "\n".join(head + [lang["none"]])
        head.append("| # | Severity | Type | CWE | OWASP | ATT&CK |")
        head.append("|---|---|---|---|---|---|")
        for i, f in enumerate(ordered, 1):
            cwe, owasp, attack = "—", "—", "—"
            import contextlib

            with contextlib.suppress(Exception):  # mapping is best-effort
                from suijin.modules.ops.lib import compliance as _comp

                cwe, owasp, attack = _comp.classify_finding(str(f.get("type", "")), str(f.get("evidence", "")))
            head.append(f"| {i} | {f.get('severity', '?')} | {f.get('type', '?')} | {cwe} | {owasp} | {attack} |")
        return "\n".join(head)
    return f"Error: unknown template {template!r} (exec|technical|compliance)"
