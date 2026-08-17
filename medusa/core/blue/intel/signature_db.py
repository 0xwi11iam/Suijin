"""Attack signature database."""
from __future__ import annotations

SIGNATURES = {
    "sqli": ["' OR '1'='1", "UNION SELECT", "1' AND SLEEP(5)", "admin'--", "'; DROP TABLE"],
    "xss": ["<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "javascript:alert(1)"],
    "ssti": ["{{7*7}}", "{{config}}", "${7*7}", "<%= 7*7 %>"],
    "path_traversal": ["../../../etc/passwd", "..\\..\\..\\windows\\win.ini"],
    "ssrf": ["169.254.169.254", "metadata.google.internal"],
    "cmdi": ["; id", "| whoami", "$(whoami)", "`id`"],
}

def match_signature(payload: str) -> list:
    matches = []
    for attack_type, sigs in SIGNATURES.items():
        for sig in sigs:
            if sig.lower() in payload.lower():
                matches.append(attack_type)
                break
    return matches
