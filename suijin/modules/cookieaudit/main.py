def audit_cookies(set_cookie=None) -> str:
    vals = set_cookie if isinstance(set_cookie, list) else [set_cookie]
    vals = [v for v in vals if v]
    if not vals:
        return "Error: set_cookie value(s) required (from response headers)"
    out = []
    for v in vals:
        name = v.split("=", 1)[0]
        low = v.lower()
        probs = []
        if "secure" not in low:
            probs.append("no Secure — sent over plain HTTP")
        if "httponly" not in low:
            probs.append("no HttpOnly — readable by JS (XSS session theft)")
        if "samesite" not in low:
            probs.append("no SameSite — CSRF surface")
        for flag, meaning in (("path=/", "root path"), ("domain=", "wide domain scope")):
            if flag in low and "session" in name.lower():
                probs.append(f"{flag} on a session cookie ({meaning})")
        out.append(f"{name}: " + ("; ".join(probs) if probs else "flags OK"))
    return "\n".join(out)
