import requests

def security_txt_check(url: str = "") -> str:
    if not url:
        return "Error: url required"
    base = url.strip().rstrip("/")
    if "://" not in base:
        base = "http://" + base
    for path in ("/.well-known/security.txt", "/security.txt"):
        try:
            r = requests.get(base + path, timeout=(4, 10), headers={"User-Agent": "suijin-policy-check"})
            if r.status_code == 200 and "Contact:" in r.text:
                issues = []
                if "Expires:" not in r.text:
                    issues.append("missing Expires (RFC 9116 requires it)")
                if not r.text.startswith("#") and "\n" in r.text and "Contact:" not in r.text.splitlines()[0]:
                    pass
                return f"security.txt at {path}:{' ' + '; '.join(issues) if issues else ' valid'}\n{r.text[:600]}"
        except requests.RequestException:
            continue
    return "No security.txt found (not required, but tells researchers where to report)."
