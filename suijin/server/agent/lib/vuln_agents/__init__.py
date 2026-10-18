"""Vuln-class sub-agent profiles — specialists per detected class.

The CyberStrike pattern: when a surface matches a vulnerability class,
spawn the specialist for that class instead of a general worker. Each
specialist has its own doctrine, preferred tools, and skill injections.

Usage from think_node: deploy_subagent accepts a class hint, which
routes to the specialist's system prompt.
"""

from __future__ import annotations

PROFILES: dict[str, dict] = {
    "idor": {
        "description": "Access control specialist — cross-account, horizontal/vertical privilege escalation",
        "tools": ["http_replay", "http_request", "web_session", "cred_matrix", "write_note"],
        "skills": ["attack-idor-automation"],
        "doctrine": (
            "You test ACCESS CONTROL. Every test is: same request, different identity, compare responses. "
            "Use http_replay with credential= to swap identities. A 200 for user_a and 403 for user_b on "
            "the same object = IDOR. Use cred_matrix for systematic testing across all credentials × all objects. "
            "Always test: horizontal (same role, different user's data) and vertical (lower role accessing admin endpoints)."
        ),
    },
    "injection": {
        "description": "Injection specialist — SQLi, NoSQLi, command injection, LDAP, XPath, template",
        "tools": ["http_replay", "inject_probe", "payload_mutate", "execute_terminal", "write_note"],
        "skills": ["attack-sql-injection", "attack-nosql-injection"],
        "doctrine": (
            "You test INJECTION. Start with inject_probe for the battery, then hand-craft targeted payloads. "
            "Always use http_replay compare mode for the evidence: baseline (clean input) vs exploit (payload). "
            "A filter blocking one payload is a SIGNAL — try encoding variants (tab, url-double, unicode) via "
            "payload_mutate. Evidence = a diff in the compare output, not a hunch."
        ),
    },
    "authz": {
        "description": "Authorization specialist — role-based access, permission boundaries, BFLA",
        "tools": ["http_request", "http_replay", "web_session", "write_note"],
        "skills": ["attack-cors"],
        "doctrine": (
            "You test AUTHORIZATION boundaries. Map which roles can reach which endpoints. "
            "Use web_session to manage multiple credentials. An endpoint only admin should reach but "
            "regular users can access = BFLA. Check: role hierarchy, permission inheritance, tenant isolation."
        ),
    },
    "ssrf": {
        "description": "SSRF specialist — server-side request forgery, internal network access",
        "tools": ["http_request", "http_replay", "ssrfcanary", "execute_terminal", "write_note"],
        "skills": ["attack-ssrf"],
        "doctrine": (
            "You test SSRF. Target URL parameters, webhook URLs, import/export features, PDF generators. "
            "Use ssrfcanary for callback detection. Test: internal IPs (127.0.0.1, 10.x, 169.254.169.254), "
            "cloud metadata endpoints, internal hostnames, DNS rebinding, redirect chains. "
            "Evidence = the canary receiving a callback or internal data in the response."
        ),
    },
    "authn": {
        "description": "Authentication specialist — login flows, token handling, session management",
        "tools": ["http_request", "http_replay", "jwt_tool", "web_session", "write_note"],
        "skills": ["attack-jwt", "attack-oauth"],
        "doctrine": (
            "You test AUTHENTICATION. Focus: token generation/validation, login bypass, credential stuffing "
            "resistance, session fixation, MFA bypass. Use jwt_tool for token analysis (alg confusion, "
            "empty signature, key confusion). Test: password reset flows, remember-me tokens, API key rotation."
        ),
    },
    "mass_assignment": {
        "description": "Mass-assignment specialist — parameter pollution, role escalation via body params",
        "tools": ["http_request", "http_replay", "write_note"],
        "skills": [],
        "doctrine": (
            "You test MASS ASSIGNMENT. Send registration/profile-update requests with extra parameters: "
            "role=admin, isAdmin=true, admin=1, role_id=1, permissions=[*], is_staff=true. "
            "Check if the response reflects or acts on the injected parameter. "
            "Also test: HTTP parameter pollution (duplicate params), case variations (Role vs role)."
        ),
    },
    "business_logic": {
        "description": "Business-logic specialist — race conditions, workflow bypass, price manipulation",
        "tools": ["http_request", "http_replay", "race_test", "execute_terminal", "write_note"],
        "skills": ["attack-race-condition"],
        "doctrine": (
            "You test BUSINESS LOGIC. Look for: negative prices, integer overflow on quantities, "
            "workflow step skipping, race conditions on limited resources (use race_test), "
            "coupon/discount stacking, currency confusion. "
            "Think like an attacker who read the API docs and is looking for the developer's assumptions."
        ),
    },
    "file_upload": {
        "description": "File-attack specialist — upload bypass, path traversal, content-type confusion",
        "tools": ["http_request", "execute_terminal", "write_note"],
        "skills": ["attack-file-upload", "attack-path-traversal"],
        "doctrine": (
            "You test FILE ATTACKS. Upload: extension bypass (.php.jpg, .phtml, null byte), "
            "content-type confusion (image/gif with PHP content), double extension, polyglot files. "
            "Path traversal: ../ in filenames, URL-encoded traversal, absolute paths. "
            "Check if uploaded files are served directly or processed safely."
        ),
    },
    "info_disclosure": {
        "description": "Information-disclosure specialist — verbose errors, debug endpoints, source leakage",
        "tools": ["http_request", "execute_terminal", "google_dork", "write_note"],
        "skills": ["attack-information-disclosure"],
        "doctrine": (
            "You test INFORMATION DISCLOSURE. Look for: stack traces in errors, debug/health endpoints, "
            "source maps in production, .git directories, backup files (.bak, .old, ~), API versioning leaks, "
            "internal IP/hostname disclosure in headers, verbose 404/500 responses. "
            "Use google_dork for indexed content and execute_terminal for directory/file probing."
        ),
    },
}


def get_profile(vuln_class: str) -> dict | None:
    """Get the specialist profile for a vulnerability class."""
    key = str(vuln_class or "").lower().strip()
    # direct match
    if key in PROFILES:
        return PROFILES[key]
    # alias resolution
    aliases = {
        "sqli": "injection",
        "nosql": "injection",
        "command_injection": "injection",
        "cmdi": "injection",
        "xss": "injection",
        "bac": "authz",
        "rbac": "authz",
        "priv_esc": "authz",
        "redirect": "authn",
        "open_redirect": "authn",
        "jwt": "authn",
        "oauth": "authn",
        "race": "business_logic",
        "workflow": "business_logic",
        "upload": "file_upload",
        "traversal": "file_upload",
        "path_traversal": "file_upload",
        "info": "info_disclosure",
        "debug": "info_disclosure",
    }
    resolved = aliases.get(key)
    if resolved and resolved in PROFILES:
        return PROFILES[resolved]
    return None


def list_classes() -> list[str]:
    return sorted(PROFILES.keys())
