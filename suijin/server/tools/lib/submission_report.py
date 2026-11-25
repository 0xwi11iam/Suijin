"""Submission-ready finding reports for HackerOne, Bugcrowd and Intigriti.

The report is the only thing the program receives: no internal finding ids,
no EXP ids, no engagement/chat names, no audit paths.  Platform-specific
files contain only that platform's fields; "other" contains all three
selector sheets plus one shared write-up.
"""
from __future__ import annotations

import re
from datetime import datetime

# ── generic taxonomy (no engagement data) ──────────────────────────────
_CWE = {
    "xss": "CWE-79",
    "open_redirect": "CWE-601",
    "ssrf": "CWE-918",
    "idor": "CWE-639",
    "auth_bypass": "CWE-287",
    "info_leak": "CWE-200",
    "csrf": "CWE-352",
    "rce": "CWE-94",
    "sqli": "CWE-89",
    "path_traversal": "CWE-22",
}
_VRT = {
    "xss": ("Cross-Site Scripting (XSS) → DOM", ["Reflected XSS", "Stored XSS"]),
    "open_redirect": ("Open Redirect", []),
    "ssrf": ("Server-Side Request Forgery (SSRF)", []),
    "idor": ("Insecure Direct Object Reference (IDOR)", []),
    "auth_bypass": ("Broken Authentication and Session Management", []),
    "info_leak": ("Information Disclosure", []),
    "csrf": ("Cross-Site Request Forgery (CSRF)", []),
    "rce": ("Remote Code Execution (RCE)", []),
    "sqli": ("SQL Injection", []),
    "path_traversal": ("Path Traversal", []),
}
_IMPACT = {
    "xss": "An attacker can run JavaScript in the victim's authenticated origin, read same-origin data and act as the victim.",
    "open_redirect": "An attacker can launder links through a trusted domain for phishing and token/code leakage.",
    "ssrf": "An attacker can make the server reach internal services and cloud metadata not exposed to the internet.",
    "idor": "An attacker can read or modify another user's records by changing an object identifier.",
    "auth_bypass": "An attacker can reach privileged functionality without valid authentication or authorization.",
    "info_leak": "An attacker can read data that should require authentication or higher privileges.",
    "csrf": "An attacker can make a victim perform state-changing actions without intent.",
    "rce": "An attacker can execute arbitrary code on the server.",
    "sqli": "An attacker can read or modify database contents, and potentially execute database functions.",
    "path_traversal": "An attacker can read files outside the intended directory.",
}
_REMEDIATION = {
    "xss": "Validate the redirect target/scheme against an explicit allowlist; never assign untrusted data to a navigation sink. Add a restrictive CSP as defence in depth.",
    "open_redirect": "Allowlist destination hosts/paths and reject non-HTTP(S) schemes; do not forward OAuth codes to an unvalidated redirect target.",
    "ssrf": "Allowlist outbound destinations and block link-local/metadata ranges at the network layer.",
    "idor": "Enforce object-level authorization on every lookup, not just authentication.",
    "auth_bypass": "Enforce authentication and role checks server-side on every privileged route.",
    "info_leak": "Require authentication/authorization and return only fields the caller is entitled to see.",
    "csrf": "Use CSRF tokens and SameSite cookies for state-changing requests.",
    "rce": "Remove unsafe execution paths and validate all input reaching interpreters.",
    "sqli": "Use parameterized queries and least-privilege database accounts.",
    "path_traversal": "Canonicalize paths and enforce a base-directory allowlist.",
}

_FAMILY = re.compile(r"EXP-\d+", re.I)
_SECRET_PATTERNS = [
    (re.compile(r"(?im)^(cookie|set-cookie|authorization|proxy-authorization):.*$"), r"\1: <redacted>"),
    (re.compile(r"(?i)(bearer)\s+[A-Za-z0-9._\-]+"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b([a-z0-9_]*(?:token|secret|password|api[_-]?key)[a-z0-9_]*)=([^&\s]+)"), r"\1=<redacted>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted-key>"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "<redacted-key>"),
]


def _redact(text: str) -> str:
    out = str(text or "")
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _class_key(raw: str, title: str = "") -> str:
    canonical = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if canonical in _CWE:
        return canonical
    text = f"{raw} {title}".lower()
    for key, words in {
        "open_redirect": ("open redirect", "redirect_uri", "unvalidated redirect"),
        "xss": ("xss", "cross-site scripting", "javascript:"),
        "ssrf": ("ssrf", "server-side request"),
        "idor": ("idor", "object reference"),
        "auth_bypass": ("auth bypass", "authentication bypass", "sso"),
        "info_leak": ("info leak", "information disclosure", "readable"),
        "csrf": ("csrf",),
        "rce": ("rce", "remote code"),
        "sqli": ("sqli", "sql injection"),
        "path_traversal": ("path traversal", "directory traversal"),
    }.items():
        if any(word in text for word in words):
            return key
    for key in _CWE:
        if key in text:
            return key
    return ""


def defaults(raw_class: str, title: str, target: str) -> dict:
    key = _class_key(raw_class, title) or "xss"
    vrt, alternatives = _VRT.get(key, ("Other", []))
    if key == "xss":
        text = f"{title} {raw_class}".lower()
        if "stored" in text:
            vrt, alternatives = "Cross-Site Scripting (XSS) → Stored", ["DOM XSS", "Reflected XSS"]
        elif "reflected" in text:
            vrt, alternatives = "Cross-Site Scripting (XSS) → Reflected", ["DOM XSS", "Stored XSS"]
    asset_type = "IP" if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", target) else "Domain"
    return {
        "class": key,
        "cwe": _CWE.get(key, "CWE-20"),
        "vrt": vrt,
        "vrt_alternatives": alternatives,
        "asset_type": asset_type,
        "cvss_version": "3.1",
        "severity_method": "CVSS",
        "tester_ip": "",
        "impact": _IMPACT.get(key, "Impact depends on the affected asset and data."),
        "remediation": _REMEDIATION.get(key, "Validate all untrusted input and enforce server-side authorization."),
    }


def _endpoint(target: str, title: str, description: str) -> str:
    for text in (title, description):
        match = re.search(r"(?<![\w.])/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", text or "")
        if match and len(match.group(0)) > 1:
            return match.group(0).rstrip(".,)")
    return target


def _command_endpoint(catalog: dict | None) -> str:
    from urllib.parse import urlparse

    for item in (catalog or {}).get("poc") or []:
        cmd = str(item.get("cmd") if isinstance(item, dict) else "")
        match = re.search(r"https?://[^\s]+", cmd)
        if not match:
            continue
        path = urlparse(match.group(0).rstrip("'\"")).path
        if path and path != "/":
            return path
    return ""


def _select(options: list[str], selected: str) -> str:
    return "  ".join(("[x] " if option == selected else "[ ] ") + option for option in options)


def _evidence(rec: dict, catalog: dict | None) -> list[str]:
    names = []
    for path in rec.get("evidencePaths") or []:
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        if name and name not in names:
            names.append(name)
    if not names:
        names = ["exploit.yaml", "run-1.log"]
    return names


def _steps(rec: dict, catalog: dict | None) -> list[str]:
    commands = []
    for item in (catalog or {}).get("poc") or []:
        cmd = item.get("cmd") if isinstance(item, dict) else str(item)
        if cmd:
            commands.append(str(cmd))
    if not commands:
        for line in (rec.get("description") or "").splitlines():
            line = line.strip()
            if line.startswith(("1.", "2.", "3.")):
                commands.append(line)
    if not commands:
        return ["Open the affected endpoint and follow the documented request below."]
    return [f"Run: `{_redact(cmd)}`" for cmd in commands]


def _raw(rec: dict, catalog: dict | None) -> str:
    commands = [str(item.get("cmd")) for item in (catalog or {}).get("poc") or [] if isinstance(item, dict) and item.get("cmd")]
    output = str((catalog or {}).get("last_run") or rec.get("raw") or "").strip()
    lines = [f"$ {_redact(cmd)}" for cmd in commands]
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("[COMMAND]", "[EXIT]", "[STDOUT]", "[sandbox] WARNING")):
            continue
        if "cd /Users/" in stripped or "cd ~/.suijin" in stripped:
            stripped = stripped.split("; ", 1)[-1]
        stripped = re.sub(r"/Users/[^\s'\"]+", "<path>", stripped)
        stripped = stripped.replace("$TMPDIR", "<tmp>")
        lines.append(stripped)
    if not lines:
        lines = ["Paste the captured request/response here."]
    return _redact("\n".join(lines)[:12000])


def _head(rec: dict, metadata: dict, platform: str) -> str:
    title = _redact(_FAMILY.sub("", rec.get("title") or "").strip())
    title = re.sub(r"^CONFIRMED\s+—\s*(?:(?:CRITICAL|HIGH|MEDIUM|LOW|INFO)\s+)?(?:CVSS\s+[\d.]+\s*)?:?\s*", "", title, flags=re.I).strip()
    title = title or "Security finding"
    lines = [f"# {title}", ""]
    if platform == "hackerone":
        lines += [
            "## HackerOne submission", "",
            f"**Asset type:** {_select(['Domain', 'Wildcard', 'URL', 'IP'], metadata['asset_type'])}",
            f"**CWE:** [x] {metadata['cwe']}",
            f"**CVSS:** {_select(['3.0', '3.1', '4.0', 'Manual'], metadata['cvss_version'])} · {rec.get('cvss') or rec.get('cvssScore') or 'n/a'}",
        ]
    elif platform == "bugcrowd":
        alternatives = [metadata["vrt"]] + metadata.get("vrt_alternatives", [])
        lines += [
            "## Bugcrowd submission", "",
            f"**VRT:** {_select(alternatives, metadata['vrt'])}",
            "**P-level:** assigned by Bugcrowd from the VRT.",
        ]
    elif platform == "intigriti":
        lines += [
            "## Intigriti submission", "",
            f"**Severity:** {_select(['CVSS', 'Manual'], metadata['severity_method'])} · {rec.get('severity') or 'n/a'} ({rec.get('cvss') or rec.get('cvssScore') or 'n/a'})",
            f"**Tester IP:** {metadata.get('tester_ip') or 'optional — add if the program asks'}",
        ]
    else:
        lines += [
            "## Platform selectors", "",
            "**HackerOne**  ",
            f"Asset type: {_select(['Domain', 'Wildcard', 'URL', 'IP'], metadata['asset_type'])}  ",
            f"CWE: [x] {metadata['cwe']}  ",
            f"CVSS: {_select(['3.0', '3.1', '4.0', 'Manual'], metadata['cvss_version'])} · {rec.get('cvss') or rec.get('cvssScore') or 'n/a'}",
            "",
            "**Bugcrowd**  ",
            f"VRT: {_select([metadata['vrt']] + metadata.get('vrt_alternatives', []), metadata['vrt'])}  ",
            "P-level: assigned by Bugcrowd from the VRT.",
            "",
            "**Intigriti**  ",
            f"Severity: {_select(['CVSS', 'Manual'], metadata['severity_method'])} · {rec.get('severity') or 'n/a'} ({rec.get('cvss') or rec.get('cvssScore') or 'n/a'})  ",
            f"Tester IP: {metadata.get('tester_ip') or 'optional — add if the program asks'}",
        ]
    return "\n".join(lines)


def _body(rec: dict, catalog: dict | None, metadata: dict) -> str:
    target = rec.get("target") or (catalog or {}).get("target") or "affected asset"
    from urllib.parse import urlparse

    asset = urlparse(target).hostname or target
    endpoint = _endpoint(target, rec.get("title") or "", rec.get("description") or "")
    if endpoint == target or endpoint == "affected asset":
        endpoint = _command_endpoint(catalog) or endpoint
    description = _redact(rec.get("description") or (catalog or {}).get("description") or rec.get("title") or "")
    # Strip Suijin-internal references from the submitted text.
    description = re.sub(r"\s*\((?:exploit\.yaml|run-\d+\.log|catalog\.json)[^)]*\)", "", description)
    description = re.sub(r"\bEXP-\d+\b", "the attached POC", description, flags=re.I)
    steps = "\n".join(f"{i}. {step}" for i, step in enumerate(_steps(rec, catalog), 1))
    raw = _raw(rec, catalog)
    evidence = "\n".join(f"- `{name}`" for name in _evidence(rec, catalog))
    return f"""## Summary
{description or 'See the technical description below.'}

## Affected asset
- Asset: {asset}
- URL: {target}
- Endpoint: {endpoint}

## Prerequisites
- Use your own test account and the role needed to reach the endpoint.
- Rate-limit requests; do not run destructive or bulk actions.

## Root cause
{description or 'The affected endpoint fails to validate or authorize the input described in the title.'}

## Steps to reproduce
{steps}

## Request / response
```http
{raw or 'Paste the captured request/response here.'}
```

## Impact
{metadata['impact']}

## Remediation
{metadata['remediation']}

## Evidence to attach
{evidence}
"""


def render(rec: dict, metadata: dict, platform: str, catalog: dict | None = None) -> str:
    platform = platform.lower()
    head = _head(rec, metadata, platform)
    body = _body(rec, catalog, metadata)
    if platform == "bugcrowd":
        description = body
        count = len(description)
        return head + "\n\n## Description (single box)\n\n" + description + f"\n\n_Character count: {count:,} / 25,000_\n"
    if platform == "hackerone":
        # H1 splits Description and Impact; keep the body together for simplicity.
        return head + "\n\n## Description\n\n" + body.split("## Impact")[0] + "\n\n## Impact\n" + body.split("## Impact", 1)[1] if "## Impact" in body else head + "\n\n" + body
    if platform == "intigriti":
        return head + "\n\n## Description / proof of concept\n\n" + body
    return head + "\n\n## Shared write-up\n\n" + body
