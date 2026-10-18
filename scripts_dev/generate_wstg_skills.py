#!/usr/bin/env python3
"""Skill generator — produces structured SKILL.md files from OWASP WSTG data.

The CyberStrike insight: they generate 200+ skills from MITRE ATT&CK and
NIST data programmatically. Suijin's 43 hand-written skills are good but
can't scale. This generator produces skill files with YAML frontmatter
(chain metadata, CWE IDs, severity boosts) from public WSTG categories.

Usage:
    .venv/bin/python scripts_dev/generate_wstg_skills.py --output suijin/server/agent/lib/skills/
"""

from __future__ import annotations

import argparse
from pathlib import Path

# WSTG categories mapped to Suijin skill format with chain metadata
# Source: OWASP Web Security Testing Guide v4.2 categories
WSTG_SKILLS = {
    "wstg-info-gathering": {
        "name": "WSTG Information Gathering",
        "category": "recon",
        "description": "Systematic information gathering: search engine discovery, web server fingerprinting, review page content for info leakage, enumerate applications, review robot.txt, fingerprint frameworks, search public files",
        "cwe_ids": ["CWE-200"],
        "chains_with": ["wstg-auth-testing", "wstg-session-mgmt"],
        "test_areas": [
            "Search engine discovery (Google dorking for the target)",
            "Web server fingerprinting (Server header, X-Powered-By, error pages)",
            "Review meta tags and comments for version/info leakage",
            "Enumerate applications on the web server (virtual hosts, subdomains)",
            "Review robots.txt and sitemap.xml for hidden paths",
            "Fingerprint the web application framework (cookies, URLs, HTML patterns)",
            "Search for public files (.git, .env, .bak, backup archives)",
        ],
    },
    "wstg-config-deployment": {
        "name": "WSTG Configuration & Deployment Management Testing",
        "category": "recon",
        "description": "Test network infrastructure config, application platform config, file handling, admin interfaces, HTTP methods, RIA cross-domain policy",
        "cwe_ids": ["CWE-16", "CWE-489"],
        "chains_with": ["wstg-auth-testing"],
        "test_areas": [
            "Test for default credentials on admin interfaces",
            "Test HTTP methods enabled (TRACE, PUT, DELETE, CONNECT)",
            "Test RIA cross-domain policy (crossdomain.xml, clientaccesspolicy.xml)",
            "Test file permission and access control on static files",
            "Test for exposed admin interfaces at common paths (/admin, /console, /manager)",
            "Test for old/backup files at predictable paths",
        ],
    },
    "wstg-identity-auth": {
        "name": "WSTG Identity & Authentication Testing",
        "category": "exploitation",
        "description": "Test credential encryption in transit, authentication bypass, weak lockout, password reset, security question, multi-factor",
        "cwe_ids": ["CWE-287", "CWE-307", "CWE-613"],
        "chains_with": ["wstg-session-mgmt", "wstg-authorization"],
        "test_areas": [
            "Test for credential transport over encrypted channel",
            "Test for user enumeration via login error messages",
            "Test for weak password policy and complexity requirements",
            "Test for authentication bypass (SQL injection in login, blank password, default creds)",
            "Test for vulnerable remember-me / persistent cookies",
            "Test for weak account lockout (brute force resistance)",
            "Test for weak password reset (predictable tokens, email enumeration)",
            "Test for weak security questions (guessable answers)",
            "Test for weak multi-factor implementation (bypassable MFA)",
            "Test for race conditions in authentication flows",
        ],
    },
    "wstg-session-mgmt": {
        "name": "WSTG Session Management Testing",
        "category": "exploitation",
        "description": "Test session tokens for entropy, predictability, fixation, logout, timeout, cookie scope",
        "cwe_ids": ["CWE-384", "CWE-613", "CWE-539"],
        "chains_with": ["wstg-identity-auth"],
        "test_areas": [
            "Test for session token entropy and predictability",
            "Test for cookies set with insecure attributes (missing HttpOnly, Secure, SameSite)",
            "Test for session fixation (token doesn't change after login)",
            "Test for session logout (session destroyed on logout)",
            "Test for session timeout (idle sessions expire)",
            "Test for session puzzlement (sessions shared across users)",
            "Test for CSRF tokens in session-modifying requests",
        ],
    },
    "wstg-authorization": {
        "name": "WSTG Authorization Testing",
        "category": "exploitation",
        "description": "Test directory traversal, privilege escalation, business logic, IDOR, horizontal/vertical access",
        "cwe_ids": ["CWE-22", "CWE-639", "CWE-284", "CWE-285"],
        "chains_with": ["wstg-injection", "attack-idor-automation"],
        "test_areas": [
            "Test directory traversal/file inclusion (../, ..\\, URL-encoded, null byte)",
            "Test for bypassing authorization schema (horizontal: same role different user)",
            "Test for privilege escalation (vertical: lower role accessing admin functions)",
            "Test for IDOR (Insecure Direct Object Reference) on all object references",
            "Test for business logic bypass (negative prices, step skipping)",
            "Test for failing open (error handling that grants access)",
            "Test for multi-tenant isolation (tenant A accessing tenant B's data)",
        ],
    },
    "wstg-data-validation": {
        "name": "WSTG Data Validation Testing",
        "category": "exploitation",
        "description": "Test reflected/stored/DOM XSS, SQL injection, LDAP injection, ORM injection, command injection, format string",
        "cwe_ids": ["CWE-79", "CWE-89", "CWE-90", "CWE-77", "CWE-134"],
        "chains_with": ["attack-sql-injection", "attack-prototype-pollution"],
        "test_areas": [
            "Test for Reflected XSS (all input vectors: URL params, HTTP headers, form fields)",
            "Test for Stored XSS (user input that renders for other users)",
            "Test for DOM-based XSS (client-side sink functions)",
            "Test for SQL Injection in all data entry points (classic, blind, time-based)",
            "Test for LDAP Injection (search filters, DN injection)",
            "Test for ORM Injection (HQL, JPQL, Criteria API)",
            "Test for OS Command Injection (metacharacters in system calls)",
            "Test for Format String Injection (%s, %n in user-controlled format strings)",
            "Test for incubated vulnerabilities (stored payloads that execute later)",
            "Test for HTTP Splitting/Smuggling (CRLF injection in headers)",
        ],
    },
    "wstg-error-handling": {
        "name": "WSTG Error Handling Testing",
        "category": "recon",
        "description": "Test for stack traces, verbose errors, CWE search, improper error handling",
        "cwe_ids": ["CWE-209", "CWE-756"],
        "chains_with": ["wstg-info-gathering"],
        "test_areas": [
            "Test for stack traces and debug information in error responses",
            "Test for improper error handling that reveals internal paths",
            "Test for CWE search in error messages (known vulnerability patterns)",
        ],
    },
    "wstg-cryptography": {
        "name": "WSTG Cryptography Testing",
        "category": "exploitation",
        "description": "Test for weak SSL/TLS, padding oracle, weak encryption, clear-text transmission",
        "cwe_ids": ["CWE-327", "CWE-326", "CWE-310"],
        "chains_with": [],
        "test_areas": [
            "Test for weak SSL/TLS ciphers and protocols",
            "Test for padding oracle vulnerabilities in encrypted responses",
            "Test for sensitive information sent via unencrypted channels",
            "Test for weak encryption keys (predictable, short, hardcoded)",
        ],
    },
    "wstg-business-logic": {
        "name": "WSTG Business Logic Testing",
        "category": "exploitation",
        "description": "Test business logic data validation, ability to forge requests, integrity checks, process timing, function limit",
        "cwe_ids": ["CWE-840", "CWE-841"],
        "chains_with": ["attack-race-condition"],
        "test_areas": [
            "Test for business logic bypass in data validation",
            "Test for ability to forge requests (price manipulation, quantity overflow)",
            "Test for integrity checks and tampering detection",
            "Test for process timing exploits (race conditions, TOCTOU)",
            "Test for function limit bypass (retry limits, rate limits, captcha bypass)",
            "Test for trust boundary violations between user and admin functions",
        ],
    },
    "wstg-client-side": {
        "name": "WSTG Client-side Testing",
        "category": "exploitation",
        "description": "Test DOM XSS, HTML5 storage, WebSocket, CORS, postMessage, local file access",
        "cwe_ids": ["CWE-79", "CWE-942", "CWE-346"],
        "chains_with": ["attack-cors", "attack-prototype-pollution"],
        "test_areas": [
            "Test for DOM-based XSS via source/sink analysis",
            "Test for HTML5 Web Storage vulnerabilities (localStorage/sessionStorage)",
            "Test for WebSocket implementation vulnerabilities",
            "Test for Cross-Origin Resource Sharing (CORS) misconfiguration",
            "Test for postMessage vulnerabilities (missing origin validation)",
            "Test for local file inclusion via client-side code",
        ],
    },
    "wstg-api-testing": {
        "name": "WSTG API Testing",
        "category": "exploitation",
        "description": "Test REST/SOAP/GraphQL APIs for mass assignment, injection, excessive data exposure, rate limiting",
        "cwe_ids": ["CWE-915", "CWE-89"],
        "chains_with": ["attack-graphql", "attack-mass-assignment"],
        "test_areas": [
            "Test for mass assignment in REST APIs (role=admin in POST body)",
            "Test for excessive data exposure (API returns more than the client needs)",
            "Test for lack of rate limiting on sensitive endpoints",
            "Test for GraphQL introspection and injection",
            "Test for SOAP XML injection and WSDL exposure",
            "Test for API versioning vulnerabilities (old versions still accessible)",
        ],
    },
}


def generate_skill(key: str, data: dict) -> str:
    """Generate a SKILL.md file content with YAML frontmatter."""
    fm_lines = [
        "---",
        f'name: {data["name"]}',
        f'description: "{data["description"][:120]}"',
        f'category: "{data["category"]}"',
        'version: "1.0"',
        'author: "suijin-wstg-generator"',
        "tags:",
    ]
    for tag in data.get("chains_with", []):
        fm_lines.append(f"  - {tag}")
    fm_lines.extend(
        [
            "cwe_ids:",
        ]
    )
    for cwe in data.get("cwe_ids", []):
        fm_lines.append(f"  - {cwe}")
    if data.get("chains_with"):
        fm_lines.append("chains_with:")
        for chain in data["chains_with"]:
            fm_lines.append(f"  - {chain}")
    fm_lines.append("---")
    fm = "\n".join(fm_lines)

    body_lines = [f"# {data['name']}", "", data["description"], "", "## Test Areas", ""]
    for i, area in enumerate(data["test_areas"], 1):
        body_lines.append(f"{i}. {area}")
    body_lines.extend(
        [
            "",
            "## Evidence Requirements",
            "- Every finding must have a baseline/exploit diff (http_replay compare)",
            "- 200 OK for both = NOT a vulnerability (no impact difference)",
            "- Filter/blocking = signal to try variants, not a dead end",
            "- Record confirmed findings with catalog_exploit",
        ]
    )
    body = "\n".join(body_lines)

    return fm + "\n\n" + body + "\n"


def main():
    ap = argparse.ArgumentParser(description="Generate WSTG-based skill files")
    ap.add_argument("--output", "-o", default=".", help="Output directory")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    count = 0
    for key, data in WSTG_SKILLS.items():
        skill_dir = out / key
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(generate_skill(key, data), encoding="utf-8")
        count += 1
        print(f"  generated: {key}")

    print(f"\n{count} skills generated in {out}")


if __name__ == "__main__":
    main()
