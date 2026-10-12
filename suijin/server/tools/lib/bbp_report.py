"""BBP-ready report generator — one file per verified finding.

Formats each confirmed vulnerability as a standalone submission ready
to paste into HackerOne, BugCrowd, or any VDP portal. No agent
reasoning, no execution trace — just the vulnerability, the evidence,
the impact, and the fix.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_CWE_MAP = {
    "ssrf": "CWE-918 (Server-Side Request Forgery)",
    "jwt_confusion": "CWE-347 (Improper Verification of Cryptographic Signature)",
    "rce": "CWE-78 (OS Command Injection)",
    "ssti": "CWE-1336 (Server-Side Template Injection)",
    "sqli": "CWE-89 (SQL Injection)",
    "xss": "CWE-79 (Cross-Site Scripting)",
    "idor": "CWE-639 (IDOR)",
    "api_key_leak": "CWE-798 (Use of Hard-coded Credentials)",
    "credential": "CWE-798 (Use of Hard-coded Credentials)",
    "path_traversal": "CWE-22 (Path Traversal)",
    "mass_assignment": "CWE-915 (Mass Assignment)",
    "open_redirect": "CWE-601 (URL Redirection to Untrusted Site)",
    "cors": "CWE-942 (Permissive Cross-domain Policy)",
    "race": "CWE-362 (Race Condition)",
    "auth_bypass": "CWE-287 (Improper Authentication)",
}

_IMPACT_MAP = {
    "ssrf": "An attacker can make the server fetch arbitrary internal resources, potentially accessing internal metadata, cloud credentials, or services that are not exposed publicly.",
    "jwt_confusion": "An attacker can forge valid tokens with arbitrary claims, escalating to any role including admin.",
    "rce": "An attacker can execute arbitrary commands on the server, leading to full system compromise.",
    "ssti": "An attacker can execute arbitrary code through template injection, potentially achieving remote code execution.",
    "sqli": "An attacker can read, modify, or delete the application's database, potentially accessing all user data.",
    "xss": "An attacker can inject scripts that execute in other users' browsers, enabling session theft, credential harvesting, or account takeover.",
    "idor": "An attacker can access other users' data by changing object identifiers, exposing potentially sensitive information.",
    "api_key_leak": "An attacker can use the exposed API key to make billable calls at the owner's expense, or access restricted services.",
    "credential": "Exposed credentials allow unauthorized access to the affected service.",
    "path_traversal": "An attacker can read arbitrary files from the server, potentially accessing configuration, credentials, or source code.",
    "mass_assignment": "An attacker can modify privileged fields (role, permissions) by including them in requests, escalating privileges.",
    "open_redirect": "An attacker can redirect users to malicious sites, enabling phishing or token theft.",
    "cors": "An attacker can read responses from any origin, potentially accessing sensitive data cross-origin.",
    "race": "An attacker can exploit timing windows to double-spend, oversell, or bypass rate limits.",
    "auth_bypass": "An attacker can bypass authentication to access protected resources without valid credentials.",
}

_REMEDIATION_MAP = {
    "ssrf": "- Validate and allowlist URLs before fetching\n- Block requests to internal/private IP ranges\n- Use a network-level egress firewall",
    "jwt_confusion": "- Pin the expected algorithm server-side\n- Never trust the algorithm from the token header\n- Use asymmetric signing (RS256) with proper key management",
    "rce": "- Sanitize all user input before execution\n- Use parameterized commands, never string concatenation\n- Run the application with minimal privileges",
    "ssti": "- Use auto-escaping templates\n- Never pass user input directly to template engines\n- Consider a sandboxed template engine",
    "sqli": "- Use parameterized queries (prepared statements)\n- Validate and sanitize all input\n- Apply least-privilege database permissions",
    "xss": "- Encode output contextually (HTML, JS, URL)\n- Use Content-Security-Policy headers\n- Validate input on both client and server",
    "idor": "- Verify object ownership on every access\n- Use indirect references (UUIDs instead of sequential IDs)\n- Implement authorization checks server-side, not client-side",
    "api_key_leak": "- Remove API keys from client-side code\n- Apply HTTP referrer restrictions\n- Use server-side proxies for API calls\n- Rotate the exposed key immediately",
    "credential": "- Remove credentials from client-accessible code\n- Use server-side authentication\n- Rotate the exposed credentials",
    "path_traversal": "- Validate and normalize file paths\n- Use a safe file API that rejects traversal\n- Run the application in a restricted directory",
    "mass_assignment": "- Whitelist allowed fields per endpoint\n- Never accept role/permission fields from client input\n- Use DTOs to control what the client can set",
    "open_redirect": "- Validate redirect targets against an allowlist\n- Never redirect to user-supplied URLs\n- Use server-side mapping for redirects",
    "cors": "- Restrict Access-Control-Allow-Origin to specific trusted origins\n- Never use wildcard (*) with credentials\n- Validate Origin headers server-side",
    "race": "- Use database transactions with proper isolation\n- Implement idempotency keys\n- Use pessimistic locking for critical sections",
    "auth_bypass": "- Verify authentication on every request\n- Use a centralized authentication middleware\n- Test authentication bypass scenarios",
}


def generate_finding_report(finding: dict, engagement_dir: Path) -> Path:
    """Generate a per-finding BBP submission file."""
    sev = str(finding.get("severity", "info")).lower()
    ftype = str(finding.get("type", "unknown")).lower()
    endpoint = str(finding.get("endpoint", ""))
    desc = str(finding.get("description", ""))
    title = str(finding.get("title", desc[:80]))
    evidence = str(finding.get("evidence", ""))

    cwe = _CWE_MAP.get(ftype, "")
    impact = _IMPACT_MAP.get(
        ftype, "This vulnerability could allow an attacker to access or modify data, or disrupt service availability."
    )
    remediation = _REMEDIATION_MAP.get(ftype, "- Follow security best practices for this vulnerability class")

    # severity display
    sev_display = sev.upper()
    if sev_display == "CRITICAL":
        sev_display = "Critical"
    elif sev_display == "HIGH":
        sev_display = "High"
    elif sev_display == "MEDIUM":
        sev_display = "Medium"
    elif sev_display == "LOW":
        sev_display = "Low"

    slug = "".join(c if c.isalnum() else "-" for c in title.lower())[:50].strip("-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    reports_dir = engagement_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{slug}_{ts}.md"

    lines = [
        f"# {title}",
        "",
        f"**Severity**: {sev_display}",
        f"**Affected URL**: {endpoint or '(see description)'}",
    ]
    if cwe:
        lines.append(f"**CWE**: {cwe}")
    lines.extend(["", "## Summary", ""])
    lines.append(desc or title)
    lines.extend(["", "## Steps to Reproduce", ""])

    # POC commands if available
    poc = finding.get("poc") or finding.get("commands") or []
    if poc:
        for i, cmd in enumerate(poc[:10], 1):
            lines.append(f"{i}. Run:")
            lines.append("```bash")
            lines.append(str(cmd))
            lines.append("```")
        expected = finding.get("expected_result") or finding.get("marker")
        if expected:
            lines.append(f"\nExpected output contains: `{expected}`")
    else:
        lines.append("(Reproduction steps from the finding evidence below)")

    lines.extend(["", "## Impact", ""])
    lines.append(impact)

    if evidence:
        lines.extend(["", "## Evidence", ""])
        lines.append("```")
        lines.append(evidence[:2000])
        lines.append("```")

    lines.extend(["", "## Remediation", ""])
    lines.append(remediation)
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def generate_bug_report(iterations: list, engagement_dir: Path) -> Path:
    """Generate the system difficulties section — agent-reported issues
    that were tool/system problems, not target findings."""
    issues = []
    for it in iterations:
        act = it.get("action") or {}
        if not act.get("success", True):
            tool = act.get("tool", "?")
            obs = str(it.get("observation", ""))[:200]
            issues.append({"iteration": it.get("iteration", "?"), "tool": tool, "issue": obs})

    lines = ["## System Difficulties", ""]
    lines.append("Issues the agent encountered that were tool/system problems,")
    lines.append("not target findings:")
    lines.append("")

    if issues:
        lines.append("| Iteration | Tool | Issue |")
        lines.append("|-----------|------|-------|")
        for iss in issues:
            lines.append(f"| {iss['iteration']} | {iss['tool']} | {iss['issue'][:80]} |")
        lines.append("")
        lines.append(f"Total: {len(issues)} tool failures.")
    else:
        lines.append("No tool failures encountered — clean run.")

    reports_dir = engagement_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "system_difficulties.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def generate_all_reports(engagement_dir: Path, findings: list, iterations: list) -> list[Path]:
    """Generate per-finding BBP reports + the system difficulties report."""
    paths = []
    for f in findings:
        if isinstance(f, dict) and str(f.get("severity", "")).lower() in ("critical", "high", "medium", "low"):
            try:
                p = generate_finding_report(f, engagement_dir)
                paths.append(p)
            except Exception as e:
                print(f"  report generation failed for finding: {e}")
    try:
        p = generate_bug_report(iterations, engagement_dir)
        paths.append(p)
    except Exception as e:
        print(f"  bug report failed: {e}")
    return paths
