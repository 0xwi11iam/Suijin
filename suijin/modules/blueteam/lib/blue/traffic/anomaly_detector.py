"""Anomaly detector — the core signal set shared by every blue surface.

Used by: the production scorer (TUI feed, battle watchdog, replay/eval,
spar), real_battle recall scoring, and MCP suijin_detect. The TUI's
pre-AI fast path (tui/feed.py) historically carried richer patterns —
command injection, JWT, deserialization, NoSQL, LDAP, mass assignment,
file inclusion, GraphQL — that this core set lacked, so every offline
metric overestimated stealth. All classes live here now; the TUI keeps
its own (slightly wider) copies as the pre-AI tier.
"""

from __future__ import annotations

import re


def detect_anomalies(request: dict, profile: dict) -> list:
    signals = []
    method = request.get("method", "GET")
    if profile.get("methods", {}).get(method, 0) == 0:
        signals.append(("unusual_method", 2, f"Method {method} never seen on this endpoint"))
    # Scan body AND query string AND raw path — GET attacks (?data={{..}},
    # ?path=../../, ?q=' OR 1=1) live outside the body. (Found by the
    # replay harness: body-only scanning gave 0.14 recall on battle traffic.)
    body = " ".join(
        [
            str(request.get("body", "")),
            str(request.get("query", "")),
            str(request.get("path", "")),
        ]
    )
    sql_patterns = re.findall(
        r"(?i)(union\s+select|'\s*or\s+'1'\s*=\s*'1|\bselect\b.*\bfrom\b|\bdrop\s+table|\binsert\s+into|'\s*--"
        r"|\bsleep\s*\(|benchmark\s*\(|pg_sleep)",
        body,
    )
    if sql_patterns:
        signals.append(("sql_injection", 4, f"SQL keywords: {sql_patterns[:3]}"))
    xss_patterns = re.findall(r"(?i)(<script|onerror\s*=|javascript:|<img[^>]+onerror|<svg/onload)", body)
    if xss_patterns:
        signals.append(("xss_attempt", 4, f"XSS patterns: {xss_patterns[:3]}"))
    traversal_patterns = re.findall(r"\.\./|\.\.\\|/etc/passwd|/etc/shadow|C:\\Windows|/winnt/|boot\.ini", body)
    if traversal_patterns:
        signals.append(("path_traversal", 3, "Path traversal attempt"))
    ssrf_patterns = re.findall(
        r"(?:169\.254\.169\.254|metadata\.google\.internal|127\.0\.0\.1:\d+|100\.64\.0\.\d+|\.cloud/metadata)", body
    )
    if ssrf_patterns:
        signals.append(("ssrf_attempt", 4, "SSRF to metadata endpoint"))
    ssti_patterns = re.findall(r"\{\{.*\}\}|\$\{.*\}", body)
    # AND-gate: bare {{...}} appears in templated marketing copy; require an
    # expression marker before firing (the TUI fast path skips this gate).
    if ssti_patterns and ("__import__" in body or "popen" in body or "7*7" in body):
        signals.append(("ssti_attempt", 4, "Template injection expression"))
    scanner_uas = re.findall(
        r"(?i)\b(sqlmap|nikto|nmap scripting|gobuster|masscan|hydra|dirbuster|nuclei|feroxbuster|wfuzz"
        r"|burpsuite|acunetix|nessus|openvas|w3af|owasp[\s_-]?zap)\b",
        str(request.get("user_agent", "")),
    )
    if scanner_uas:
        signals.append(("scanner_ua", 3, f"Scanner user-agent: {scanner_uas[:2]}"))
    xxe_patterns = re.findall(r"(?i)(<!entity|<!doctype\s+\w+\s*\[)", body)
    if xxe_patterns:
        signals.append(("xxe_attempt", 5, "XML external entity declaration"))
    cmdi_patterns = re.findall(
        r"(?i)(;\s*(id|whoami|uname|cat\s+/etc|ls\s+-la|pwd|wget\s+|curl\s+)\b|\|\s*(id|whoami|cat)\b"
        r"|`[^`]{2,}`|\$\([^)]{2,}\))",
        body,
    )
    if cmdi_patterns:
        signals.append(("command_injection", 4, f"Shell metacharacter chains: {cmdi_patterns[:2]}"))
    jwt_patterns = re.findall(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", body)
    if jwt_patterns:
        signals.append(("jwt_attack", 3, "Raw JWT token in request (tampering surface)"))
    deser_patterns = re.findall(r"(?i)(O:\d+:\"[^\"]+\":\d+:|pickle\.loads|yaml\.load\s*\(|marshal\.loads)", body)
    if deser_patterns:
        signals.append(("deserialization", 5, "Unsafe deserialization payload"))
    ldap_patterns = re.findall(r"(?i)(\(\s*&\s*\(|\*\s*\)\(\s*\|)", body)
    if ldap_patterns:
        signals.append(("ldap_injection", 4, "LDAP filter metacharacters"))
    # optional closing quote: matches form syntax (pass[$ne]=) AND raw JSON keys ("$ne":)
    nosql_patterns = re.findall(r"(?i)(\$\s*(ne|gt|lt|gte|lte|in|nin|regex|where|or|and)\s*\"?\s*:)", body)
    if nosql_patterns:
        signals.append(("nosql_injection", 4, "NoSQL operator injection"))
    massassign_patterns = re.findall(
        r"(?:\"role\"\s*:\s*\"(?:admin|root|superuser)\"|\"is_admin\"\s*:\s*true|\"isAdmin\"\s*:\s*true)", body
    )
    if massassign_patterns:
        signals.append(("mass_assignment", 4, "Privilege field in request payload"))
    inclusion_patterns = re.findall(r"(?i)(php://filter|php://input|data://text|expect://|file:///etc)", body)
    if inclusion_patterns:
        signals.append(("file_inclusion", 5, "Wrapper-based file inclusion"))
    graphql_patterns = re.findall(r"(?i)(__schema|__typename|fragment\s+on|\bquery\s*\{|\bmutation\s*\{)", body)
    if graphql_patterns:
        signals.append(("graphql_recon", 3, f"GraphQL introspection/recon: {graphql_patterns[:2]}"))
    brute_uas = re.findall(r"(?i)\b(hydra|medusa|ncrack|patator|crowbar|suijin)\b", str(request.get("user_agent", "")))
    if brute_uas:
        signals.append(("brute_force", 3, f"Brute-force tool user-agent: {brute_uas[:2]}"))
    headers = request.get("headers") or {}
    header_text = (
        " ".join(f"{k}:{v}" for k, v in headers.items()).lower() if isinstance(headers, dict) else str(headers).lower()
    )
    if re.search(r"(?i)x-admin\s*:\s*true|x-role\s*:\s*admin", header_text):
        signals.append(("auth_bypass_header", 5, "Privilege-spoofing header"))
    return signals
