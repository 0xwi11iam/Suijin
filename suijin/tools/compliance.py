"""Compliance mapping — finding classes to CWE / OWASP Top-10 / MITRE ATT&CK.

A pure lookup over finding types + description keywords. Deliberately
standalone (`suijin compliance [engagement]`): no changes to the report
pipeline, no state, no hot paths — a table and two functions.
"""

from __future__ import annotations

from pathlib import Path

from suijin.tools.workspace import WORKSPACE_DIR

# keyword -> (CWE, OWASP Top-10 2021 category, MITRE ATT&CK technique)
# Keywords are checked against "<type> <description>" lowercased; the FIRST
# match wins, so order matters (specific before generic).
MAPPING: list[tuple[str, str, str, str, str]] = [
    # keyword              CWE              OWASP 2021                     ATT&CK
    ("sql injection", "CWE-89", "A03 Injection", "T1190"),
    ("sqli", "CWE-89", "A03 Injection", "T1190"),
    ("blind sqli", "CWE-89", "A03 Injection", "T1190"),
    ("xss", "CWE-79", "A03 Injection", "T1189"),
    ("ssti", "CWE-1336", "A03 Injection", "T1059"),
    ("command injection", "CWE-78", "A03 Injection", "T1059"),
    ("xxe", "CWE-611", "A05 Security Misconfiguration", "T1055"),
    ("deserial", "CWE-502", "A08 Software and Data Integrity", "T1203"),
    ("path traversal", "CWE-22", "A01 Broken Access Control", "T1083"),
    ("file inclusion", "CWE-98", "A03 Injection", "T1105"),
    ("lfi", "CWE-98", "A03 Injection", "T1105"),
    ("ssrf", "CWE-918", "A10 Server-Side Request Forgery", "T1190"),
    ("idor", "CWE-639", "A01 Broken Access Control", "T1210"),
    ("auth bypass", "CWE-287", "A07 Identification and Authentication", "T1550"),
    ("jwt", "CWE-347", "A07 Identification and Authentication", "T1550"),
    ("mass assignment", "CWE-915", "A04 Insecure Design", "T1550"),
    ("race condition", "CWE-362", "A04 Insecure Design", "T1068"),
    ("rate limit", "CWE-770", "A04 Insecure Design", "T1499"),
    ("info disclosure", "CWE-200", "A05 Security Misconfiguration", "T1592"),
    ("info leak", "CWE-200", "A05 Security Misconfiguration", "T1592"),
    ("waf", "CWE-693", "A05 Security Misconfiguration", "T1590"),
    ("misconfigur", "CWE-16", "A05 Security Misconfiguration", "T1584"),
    ("upload bypass", "CWE-434", "A04 Insecure Design", "T1105"),
    ("file upload", "CWE-434", "A04 Insecure Design", "T1105"),
    ("csrf", "CWE-352", "A01 Broken Access Control", "T1185"),
    ("privilege escalation", "CWE-269", "A01 Broken Access Control", "T1548"),
    ("privesc", "CWE-269", "A01 Broken Access Control", "T1548"),
    ("credential", "CWE-798", "A07 Identification and Authentication", "T1552"),
    ("secret", "CWE-798", "A07 Identification and Authentication", "T1552"),
]
_FALLBACK = ("CWE-693", "A05 Security Misconfiguration", "T1595")  # unmapped → generic


def classify_finding(finding_type: str, description: str = "") -> tuple[str, str, str]:
    """(CWE, OWASP, ATT&CK) for one finding. First keyword hit wins.

    Finding types are snake_case (path_traversal) while keywords use
    spaces — underscores are normalized before matching.
    """
    text = f"{finding_type} {description}".lower().replace("_", " ")
    for keyword, cwe, owasp, attack in MAPPING:
        if keyword in text:
            return cwe, owasp, attack
    return _FALLBACK


def map_findings(findings: list[dict]) -> list[dict]:
    """Annotate findings with compliance mappings."""
    out = []
    for f in findings or []:
        cwe, owasp, attack = classify_finding(str(f.get("type", "")), str(f.get("description", "")))
        out.append({**f, "cwe": cwe, "owasp": owasp, "attack": attack})
    return out


def summarize(mapped: list[dict]) -> dict:
    """Counts per framework for the summary table."""
    counts: dict[str, dict[str, int]] = {"cwe": {}, "owasp": {}, "attack": {}}
    for f in mapped:
        for key in counts:
            counts[key][f[key]] = counts[key].get(f[key], 0) + 1
    return counts


def load_findings(engagement: str | None = None, workspace: Path | None = None) -> list[dict]:
    """Findings from audit trails; newest engagement when name omitted."""
    ws = Path(workspace) if workspace else WORKSPACE_DIR / "audit_trails"
    trails = sorted(ws.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if ws.is_dir() else []
    if engagement:
        needle = engagement.lower().replace(" ", "_")
        trails = [t for t in trails if needle in t.stem.lower()] or trails
    findings: list[dict] = []
    for t in trails:
        try:
            import json

            data = json.loads(t.read_text())
        except (OSError, ValueError):
            continue
        findings.extend(data.get("findings", []))
        if engagement:
            break  # named engagement: first matching trail only
    return findings


def render(mapped: list[dict]) -> str:
    if not mapped:
        return (
            "No findings recorded — compliance map is empty. "
            "Findings land in suijin_agent/audit_trails/ during engagements."
        )
    s = summarize(mapped)
    lines = [
        f"Compliance map — {len(mapped)} finding(s)",
        "",
        f"  {'finding':28} {'sev':8} {'CWE':10} {'OWASP 2021':38} ATT&CK",
    ]
    for f in mapped:
        desc = (f.get("description") or f.get("type") or "?")[:28]
        lines.append(f"  {desc:28} {str(f.get('severity', '?'))[:8]:8} {f['cwe']:10} {f['owasp']:38} {f['attack']}")
    lines.append("")
    lines.append("By OWASP Top-10 2021:")
    for cat, n in sorted(s["owasp"].items(), key=lambda x: -x[1]):
        lines.append(f"  {n:>3}x {cat}")
    lines.append("")
    lines.append("By ATT&CK technique:")
    for t, n in sorted(s["attack"].items(), key=lambda x: -x[1]):
        lines.append(f"  {n:>3}x {t}")
    return "\n".join(lines)


# provenance for tests: every mapped keyword must be findable & non-empty
assert all(k and c and o and a for k, c, o, a in MAPPING)
