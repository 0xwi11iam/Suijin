"""
Skill loader — injects attack-skill-specific workflows into the system prompt.
"""
from medusa.skills.sql_injection import SQLI_SKILL_PROMPT
from medusa.skills.xss import XSS_SKILL_PROMPT
from medusa.skills.rce import RCE_SKILL_PROMPT
from medusa.skills.path_traversal import PATH_TRAVERSAL_SKILL_PROMPT
from medusa.skills.ssrf import SSRF_SKILL_PROMPT
from medusa.skills.access_control import ACCESS_CONTROL_SKILL_PROMPT
from medusa.skills.cve_exploit import CVE_EXPLOIT_SKILL_PROMPT
from medusa.skills.post_exploit import POST_EXPLOIT_SKILL_PROMPT
# ── New skills ───────────────────────────────────────────────────────
from medusa.skills.xxe import XXE_SKILL_PROMPT
from medusa.skills.nosql_injection import NOSQL_SKILL_PROMPT
from medusa.skills.jwt_attacks import JWT_SKILL_PROMPT
from medusa.skills.file_upload import FILE_UPLOAD_SKILL_PROMPT
from medusa.skills.mass_assignment import MASS_ASSIGNMENT_SKILL_PROMPT
from medusa.skills.csrf import CSRF_SKILL_PROMPT
from medusa.skills.cors import CORS_SKILL_PROMPT
from medusa.skills.prototype_pollution import PROTOTYPE_POLLUTION_SKILL_PROMPT
from medusa.skills.deserialization import DESERIALIZATION_SKILL_PROMPT
from medusa.skills.graphql_attacks import GRAPHQL_SKILL_PROMPT
from medusa.skills.race_condition import RACE_CONDITION_SKILL_PROMPT
from medusa.skills.subdomain_takeover import SUBDOMAIN_TAKEOVER_SKILL_PROMPT
from medusa.skills.cache_poisoning import CACHE_POISONING_SKILL_PROMPT
from medusa.skills.host_header import HOST_HEADER_SKILL_PROMPT
from medusa.skills.spring_boot import SPRING_BOOT_SKILL_PROMPT
from medusa.skills.django import DJANGO_SKILL_PROMPT
from medusa.skills.wordpress import WORDPRESS_SKILL_PROMPT
from medusa.skills.ldap_injection import LDAP_INJECTION_SKILL_PROMPT
from medusa.skills.crlf_injection import CRLF_SKILL_PROMPT
from medusa.skills.parameter_pollution import PARAMETER_POLLUTION_SKILL_PROMPT
from medusa.skills.open_redirect import OPEN_REDIRECT_SKILL_PROMPT
from medusa.skills.information_disclosure import INFO_DISCLOSURE_SKILL_PROMPT
from medusa.skills.linux_privesc import LINUX_PRIVESC_SKILL_PROMPT
from medusa.skills.container_escape import CONTAINER_ESCAPE_SKILL_PROMPT
from medusa.skills.saml import SAML_SKILL_PROMPT
from medusa.skills.oauth import OAUTH_SKILL_PROMPT
from medusa.skills.twofa_bypass import TWOFA_BYPASS_SKILL_PROMPT
from medusa.skills.clickjacking import CLICKJACKING_SKILL_PROMPT
from medusa.skills.dom_clobbering import DOM_CLOBBERING_SKILL_PROMPT
from medusa.skills.websocket import WEBSOCKET_SKILL_PROMPT
from medusa.skills.xpath_injection import XPATH_INJECTION_SKILL_PROMPT
from medusa.skills.email_header import EMAIL_HEADER_SKILL_PROMPT
from medusa.skills.redos import REDOS_SKILL_PROMPT
from medusa.skills.http_smuggling import HTTP_SMUGGLING_SKILL_PROMPT
from medusa.skills.dns_rebinding import DNS_REBINDING_SKILL_PROMPT
from medusa.skills.soul import SOUL_SKILL_PROMPT

SKILL_MAP = {
    # Original 8
    "sql_injection": SQLI_SKILL_PROMPT, "sqli": SQLI_SKILL_PROMPT,
    "xss": XSS_SKILL_PROMPT,
    "rce": RCE_SKILL_PROMPT, "command_injection": RCE_SKILL_PROMPT,
    "path_traversal": PATH_TRAVERSAL_SKILL_PROMPT, "lfi": PATH_TRAVERSAL_SKILL_PROMPT, "file_inclusion": PATH_TRAVERSAL_SKILL_PROMPT,
    "ssrf": SSRF_SKILL_PROMPT,
    "access_control": ACCESS_CONTROL_SKILL_PROMPT, "idor": ACCESS_CONTROL_SKILL_PROMPT, "auth_bypass": ACCESS_CONTROL_SKILL_PROMPT,
    "cve_exploit": CVE_EXPLOIT_SKILL_PROMPT, "cve": CVE_EXPLOIT_SKILL_PROMPT,
    "post_exploit": POST_EXPLOIT_SKILL_PROMPT, "post_exploitation": POST_EXPLOIT_SKILL_PROMPT, "persistence": POST_EXPLOIT_SKILL_PROMPT, "lateral_movement": POST_EXPLOIT_SKILL_PROMPT,
    # ── New: Injection family ──
    "xxe": XXE_SKILL_PROMPT, "xml_external_entity": XXE_SKILL_PROMPT,
    "nosql_injection": NOSQL_SKILL_PROMPT, "nosql": NOSQL_SKILL_PROMPT, "mongodb_injection": NOSQL_SKILL_PROMPT,
    # ── New: Auth & Session ──
    "jwt": JWT_SKILL_PROMPT, "jwt_attack": JWT_SKILL_PROMPT,
    # ── New: Business Logic ──
    "file_upload": FILE_UPLOAD_SKILL_PROMPT, "unrestricted_upload": FILE_UPLOAD_SKILL_PROMPT,
    "mass_assignment": MASS_ASSIGNMENT_SKILL_PROMPT, "auto_binding": MASS_ASSIGNMENT_SKILL_PROMPT,
    # ── New: Client-Side ──
    "csrf": CSRF_SKILL_PROMPT,
    "cors": CORS_SKILL_PROMPT, "cors_misconfiguration": CORS_SKILL_PROMPT,
    "prototype_pollution": PROTOTYPE_POLLUTION_SKILL_PROMPT, "proto_pollution": PROTOTYPE_POLLUTION_SKILL_PROMPT,
    # ── New: Advanced ──
    "deserialization": DESERIALIZATION_SKILL_PROMPT, "insecure_deserialization": DESERIALIZATION_SKILL_PROMPT,
    "graphql": GRAPHQL_SKILL_PROMPT, "graphql_attack": GRAPHQL_SKILL_PROMPT,
    "race_condition": RACE_CONDITION_SKILL_PROMPT, "toctou": RACE_CONDITION_SKILL_PROMPT,
    # ── New: Infrastructure ──
    "subdomain_takeover": SUBDOMAIN_TAKEOVER_SKILL_PROMPT,
    "cache_poisoning": CACHE_POISONING_SKILL_PROMPT, "web_cache_poisoning": CACHE_POISONING_SKILL_PROMPT,
    "host_header": HOST_HEADER_SKILL_PROMPT, "host_header_injection": HOST_HEADER_SKILL_PROMPT,
    # ── New: Framework-Specific ──
    "spring_boot": SPRING_BOOT_SKILL_PROMPT,
    "django": DJANGO_SKILL_PROMPT,
    "wordpress": WORDPRESS_SKILL_PROMPT,
    # ── New: Additional injection/infrastructure ──
    "ldap_injection": LDAP_INJECTION_SKILL_PROMPT, "ldap": LDAP_INJECTION_SKILL_PROMPT,
    "crlf": CRLF_SKILL_PROMPT, "crlf_injection": CRLF_SKILL_PROMPT,
    "parameter_pollution": PARAMETER_POLLUTION_SKILL_PROMPT, "hpp": PARAMETER_POLLUTION_SKILL_PROMPT,
    "open_redirect": OPEN_REDIRECT_SKILL_PROMPT,
    "information_disclosure": INFO_DISCLOSURE_SKILL_PROMPT, "info_disclosure": INFO_DISCLOSURE_SKILL_PROMPT,
    # ── New: Post-Exploitation ──
    "linux_privesc": LINUX_PRIVESC_SKILL_PROMPT, "privilege_escalation": LINUX_PRIVESC_SKILL_PROMPT,
    "container_escape": CONTAINER_ESCAPE_SKILL_PROMPT, "docker_escape": CONTAINER_ESCAPE_SKILL_PROMPT,
    # ── New: Auth/Session/Advanced ──
    "saml": SAML_SKILL_PROMPT, "saml_attack": SAML_SKILL_PROMPT,
    "oauth": OAUTH_SKILL_PROMPT, "oidc": OAUTH_SKILL_PROMPT, "oauth2": OAUTH_SKILL_PROMPT,
    "2fa_bypass": TWOFA_BYPASS_SKILL_PROMPT, "mfa_bypass": TWOFA_BYPASS_SKILL_PROMPT,
    "clickjacking": CLICKJACKING_SKILL_PROMPT, "ui_redressing": CLICKJACKING_SKILL_PROMPT,
    "dom_clobbering": DOM_CLOBBERING_SKILL_PROMPT,
    "websocket": WEBSOCKET_SKILL_PROMPT, "cswsh": WEBSOCKET_SKILL_PROMPT,
    "xpath_injection": XPATH_INJECTION_SKILL_PROMPT, "xpath": XPATH_INJECTION_SKILL_PROMPT,
    "email_header": EMAIL_HEADER_SKILL_PROMPT, "email_injection": EMAIL_HEADER_SKILL_PROMPT,
    "redos": REDOS_SKILL_PROMPT, "regex_dos": REDOS_SKILL_PROMPT,
    "http_smuggling": HTTP_SMUGGLING_SKILL_PROMPT, "request_smuggling": HTTP_SMUGGLING_SKILL_PROMPT,
    "dns_rebinding": DNS_REBINDING_SKILL_PROMPT,
    # ── Browser-first ──
    "soul": SOUL_SKILL_PROMPT, "browser": SOUL_SKILL_PROMPT, "spa": SOUL_SKILL_PROMPT,
    "react": SOUL_SKILL_PROMPT, "nextjs": SOUL_SKILL_PROMPT, "remix": SOUL_SKILL_PROMPT,
    "vue": SOUL_SKILL_PROMPT, "angular": SOUL_SKILL_PROMPT, "svelte": SOUL_SKILL_PROMPT,
    "js_heavy": SOUL_SKILL_PROMPT, "javascript": SOUL_SKILL_PROMPT,
}

# Default recon prompt — used when no specific attack path is set
DEFAULT_RECON_PROMPT = """
## PHASE: RECONNAISSANCE

You are in the **informational** phase. Your job is to MAP the target thoroughly
BEFORE attempting any exploitation.

### MANDATORY RECON WORKFLOW:

1. **Port scan**: `execute_terminal` with nmap -sV -sC TARGET
2. **Directory bruteforce**: gobuster or feroxbuster against web ports
3. **Service fingerprint**: whatweb, curl -I to identify technologies
4. **CVE research**: search_cve for every discovered service+version
5. **Endpoint discovery**: check robots.txt, sitemap.xml, .well-known/, /api/
6. **Parameter discovery**: look for forms, query strings, API parameters

### EXPLORATION RULES:
- Test EVERY endpoint you find — /login, /api, /admin, /reset, /debug
- When you find one endpoint, check for similar ones (/v1/ → also try /v2/, /internal/)
- Surface multiply: SQLi on /login failed? Try /search, /api/users, /profile?id=
- NEVER run exploits (sqlmap, hydra, msf_run) until 3+ recon steps are complete
- Log everything with write_note — negative results prevent expensive re-testing
"""


def get_skill_prompt(attack_path_type: str) -> str:
    """Return the skill-specific prompt for the given attack path.

    Args:
        attack_path_type: e.g. 'sql_injection', 'xss', 'rce', 'path_traversal',
                          or ''/None for default recon.

    Returns:
        Skill-specific prompt string.
    """
    if not attack_path_type:
        return DEFAULT_RECON_PROMPT

    # Normalize: handle 'sqli-unclassified' → 'sqli'
    normalized = attack_path_type.lower().replace("-unclassified", "").replace(" ", "_")
    prompt = SKILL_MAP.get(normalized)
    if prompt:
        return prompt

    # Partial match
    for key, value in SKILL_MAP.items():
        if key in normalized or normalized in key:
            return value

    return DEFAULT_RECON_PROMPT


def get_available_skills() -> list[str]:
    """Return list of available skill names."""
    return list(SKILL_MAP.keys())
