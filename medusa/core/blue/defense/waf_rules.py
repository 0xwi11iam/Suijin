"""WAF rule generator — ModSecurity/Cloudflare rules from attack patterns."""
from __future__ import annotations
import re


def _escape_rx(pattern: str) -> str:
    """Escape quotes and backslashes in patterns for ModSecurity @rx directives."""
    return pattern.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def generate_waf_rule(attack_type: str, pattern: str) -> str:
    safe = _escape_rx(pattern)
    if attack_type == "sqli":
        return f'SecRule REQUEST_BODY "@rx {safe}" "id:10001,deny,status:403,msg:\'SQL Injection detected\'"'
    if attack_type == "xss":
        return f'SecRule REQUEST_BODY "@rx {safe}" "id:10002,deny,status:403,msg:\'XSS detected\'"'
    return f'SecRule REQUEST_URI "@rx {safe}" "id:10003,deny,status:403"'
