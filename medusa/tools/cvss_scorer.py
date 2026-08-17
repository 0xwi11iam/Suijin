"""
medusa/tools/cvss_scorer.py — CVSS 3.1 calculator for finding severity scoring.
"""

from __future__ import annotations


# CVSS 3.1 base metrics
def calculate_cvss(
    attack_vector: str = "N",  # N=Network, A=Adjacent, L=Local, P=Physical
    attack_complexity: str = "L",  # L=Low, H=High
    privileges_required: str = "N",  # N=None, L=Low, H=High
    user_interaction: str = "N",  # N=None, R=Required
    scope: str = "U",  # U=Unchanged, C=Changed
    confidentiality: str = "N",  # N=None, L=Low, H=High
    integrity: str = "N",  # N=None, L=Low, H=High
    availability: str = "N",  # N=None, L=Low, H=High
) -> dict:
    """Calculate CVSS 3.1 base score. Returns dict with score, severity, vector."""
    # Metric weights (CVSS 3.1 spec)
    av_weights = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20}
    ac_weights = {"L": 0.77, "H": 0.44}
    pr_weights_u = {"N": 0.85, "L": 0.62, "H": 0.27}
    pr_weights_c = {"N": 0.85, "L": 0.68, "H": 0.50}
    ui_weights = {"N": 0.85, "R": 0.62}
    cia_weights = {"N": 0.00, "L": 0.22, "H": 0.56}

    # Impact sub-score
    c_val = cia_weights.get(confidentiality.upper(), 0)
    i_val = cia_weights.get(integrity.upper(), 0)
    a_val = cia_weights.get(availability.upper(), 0)
    iss = 1 - ((1 - c_val) * (1 - i_val) * (1 - a_val))

    impact = 7.52 * (iss - 0.029) - 3.25 * pow(iss - 0.02, 15) if scope.upper() == "C" else 6.42 * iss

    # Exploitability sub-score
    av = av_weights.get(attack_vector.upper(), 0.85)
    ac = ac_weights.get(attack_complexity.upper(), 0.77)
    pr = (
        pr_weights_u.get(privileges_required.upper(), 0.85)
        if scope.upper() == "U"
        else pr_weights_c.get(privileges_required.upper(), 0.85)
    )
    ui = ui_weights.get(user_interaction.upper(), 0.85)
    exploitability = 8.22 * av * ac * pr * ui

    # Base score
    if impact <= 0:
        score = 0.0
    elif scope.upper() == "U":
        score = min(impact + exploitability, 10)
    else:
        score = min(1.08 * (impact + exploitability), 10)

    score = round(score, 1)

    # Severity rating
    if score >= 9.0:
        severity = "Critical"
    elif score >= 7.0:
        severity = "High"
    elif score >= 4.0:
        severity = "Medium"
    elif score >= 0.1:
        severity = "Low"
    else:
        severity = "None"

    vector = f"CVSS:3.1/AV:{attack_vector}/AC:{attack_complexity}/PR:{privileges_required}/UI:{user_interaction}/S:{scope}/C:{confidentiality}/I:{integrity}/A:{availability}"

    return {"score": score, "severity": severity, "vector_string": vector}


def auto_score_finding(finding_type: str, details: dict = None) -> dict:
    """Auto-score a finding based on type and details. Returns CVSS dict."""
    defaults = {
        "sqli": {"C": "H", "I": "H", "A": "N", "PR": "N", "UI": "N"},
        "xss": {"C": "L", "I": "L", "A": "N", "PR": "N", "UI": "R"},
        "ssti": {"C": "H", "I": "H", "A": "H", "PR": "N", "UI": "N"},
        "rce": {"C": "H", "I": "H", "A": "H", "PR": "N", "UI": "N"},
        "ssrf": {"C": "H", "I": "N", "A": "N", "PR": "L", "UI": "N"},
        "idor": {"C": "H", "I": "N", "A": "N", "PR": "L", "UI": "N"},
        "path_traversal": {"C": "H", "I": "N", "A": "N", "PR": "N", "UI": "N"},
        "jwt_attack": {"C": "H", "I": "H", "A": "N", "PR": "N", "UI": "N"},
        "file_upload": {"C": "H", "I": "H", "A": "H", "PR": "L", "UI": "N"},
        "xxe": {"C": "H", "I": "N", "A": "N", "PR": "N", "UI": "N"},
        "csrf": {"C": "L", "I": "L", "A": "N", "PR": "N", "UI": "R"},
        "information_disclosure": {"C": "L", "I": "N", "A": "N", "PR": "N", "UI": "N"},
    }
    params = defaults.get(finding_type.lower(), {"C": "L", "I": "L", "A": "N", "PR": "N", "UI": "N"})
    if details:
        params.update({k: v for k, v in details.items() if k in params})
    return calculate_cvss(
        confidentiality=params.get("C", "N"),
        integrity=params.get("I", "N"),
        availability=params.get("A", "N"),
        privileges_required=params.get("PR", "N"),
        user_interaction=params.get("UI", "N"),
    )


def curl_snippet(method: str, url: str, headers: dict = None, body: str = None, cookies: dict = None) -> str:
    """Generate a copy-pasteable cURL command for reproducing a finding."""
    parts = ["curl", "-s"]
    if method.upper() != "GET":
        parts.append(f"-X {method.upper()}")
    if headers:
        for k, v in headers.items():
            parts.append(f"-H '{k}: {v}'")
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        parts.append(f"-b '{cookie_str}'")
    if body:
        parts.append(f"-d '{body}'")
    parts.append(f"'{url}'")
    return " \\\n  ".join(parts)
