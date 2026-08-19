_WANT = {
    "strict-transport-security": "HSTS — forces HTTPS (set on 200s over TLS)",
    "content-security-policy": "CSP — the XSS backstop; check for unsafe-inline/unsafe-eval",
    "x-content-type-options": "nosniff stops MIME confusion",
    "x-frame-options": "clickjacking guard (deny/sameorigin) unless CSP frame-ancestors present",
    "referrer-policy": "leak control (strict-origin-when-cross-origin minimum)",
    "permissions-policy": "locks camera/geo/mic from embedded content",
}


def audit_security_headers(headers: dict = None, url: str = "") -> str:
    if not isinstance(headers, dict) or not headers:
        return "Error: headers dict required (pass the headers from http_request)"
    h = {str(k).lower(): str(v) for k, v in headers.items()}
    issues = []
    for name, why in _WANT.items():
        if name not in h:
            issues.append(f"MISSING {name}: {why}")
        elif name == "content-security-policy" and ("unsafe-inline" in h[name] or "unsafe-eval" in h[name]):
            issues.append(f"WEAK CSP: {h[name][:120]}")
    for leak in ("server", "x-powered-by", "x-aspnet-version", "x-generator"):
        if leak in h:
            issues.append(f"INFO LEAK: {leak}: {h[leak]}")
    csp = h.get("content-security-policy", "")
    if "x-frame-options" not in h and "frame-ancestors" not in csp:
        issues.append("FRAMING: neither XFO nor CSP frame-ancestors — clickjacking possible")
    ctx = f" for {url}" if url else ""
    return (
        f"Header audit{ctx}:\n"
        + ("\n".join(f"- {i}" for i in issues) if issues else "all key security headers present")
        + f"\n({len(h)} headers reviewed)"
    )
