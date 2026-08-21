import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def security_txt_check(url: str = "") -> str:
    if not url:
        return "Error: url required"
    base = url.strip().rstrip("/")
    if "://" not in base:
        base = "http://" + base
    for path in ("/.well-known/security.txt", "/security.txt"):
        try:
            r = requests.get(base + path, timeout=(4, 10), headers={"User-Agent": _stealth_ua()})
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
