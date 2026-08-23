"""HeaderPeek — static security-header analysis tools.

Pure functions over header dicts: no network, no filesystem, no binaries.
"""


def header_audit(headers: dict = None) -> str:
    """Audit security-relevant response headers and flag the missing/weak ones."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    checks = {
        "strict-transport-security": ("HSTS", "long max-age recommended (>= 15552000)"),
        "content-security-policy": ("CSP", "absence leaves XSS mitigations to the app alone"),
        "x-content-type-options": ("nosniff", "should be 'nosniff'"),
        "x-frame-options": ("frame denial", "or use CSP frame-ancestors"),
        "referrer-policy": ("referrer policy", "'strict-origin-when-cross-origin' or stricter"),
        "permissions-policy": ("permissions policy", "lock down camera/geolocation/etc"),
    }
    lines = []
    for key, (label, note) in checks.items():
        v = h.get(key)
        if v is None:
            lines.append(f"  MISSING  {label:16} {note}")
        elif key == "x-content-type-options" and v.lower() != "nosniff":
            lines.append(f"  WEAK     {label:16} value {v!r} (want nosniff)")
        elif key == "strict-transport-security":
            import re

            m = re.search(r"max-age=(\d+)", v or "")
            age = int(m.group(1)) if m else 0
            state = "OK " if age >= 15552000 else "WEAK"
            lines.append(f"  {state:8} HSTS            max-age={age}")
        else:
            lines.append(f"  OK       {label:16} present")
    return "Security header audit:\n" + "\n".join(lines)


def cors_verdict(headers: dict = None) -> str:
    """Verdict a CORS configuration from response headers (the wildcard+credentials combo is the bug)."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    acao = h.get("access-control-allow-origin", "")
    acac = h.get("access-control-allow-credentials", "")
    if not acao:
        return "No CORS headers — cross-origin reads default-denied (browser SOP)."
    if acao.strip() == "*":
        if acac.lower() == "true":
            return "CRITICAL: wildcard origin WITH credentials — any origin can read authenticated responses."
        return "Wildcard origin (no credentials) — public data only; check for auth'd endpoints sharing the header."
    if acao.startswith("https://"):
        creds = " with credentials" if acac.lower() == "true" else ""
        return f"Reflects/specific origin {acao}{creds} — verify the origin allowlist can't be tricked (null, subdomain takeover)."
    return f"Unusual ACAO value {acao!r} — review manually."


def security_score(headers: dict = None) -> str:
    """Score the header posture 0-100 from the six core security headers."""
    h = {str(k).lower() for k, v in (headers or {}).items()}
    weights = {
        "strict-transport-security": 25,
        "content-security-policy": 25,
        "x-content-type-options": 15,
        "x-frame-options": 15,
        "referrer-policy": 10,
        "permissions-policy": 10,
    }
    score = sum(w for k, w in weights.items() if k in h)
    grade = "A" if score >= 85 else "B" if score >= 65 else "C" if score >= 40 else "D"
    return f"Header posture: {score}/100 (grade {grade}) — missing: {', '.join(k for k in weights if k not in h) or 'none'}"
