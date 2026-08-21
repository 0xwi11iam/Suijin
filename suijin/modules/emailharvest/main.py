import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_T = (5, 20)
_UA = {"User-Agent": _stealth_ua()}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def harvest_emails(text: str = "", url: str = "") -> str:
    import re

    if url and not text:
        try:
            text = _get(url.strip()).text
        except requests.RequestException as e:
            return f"Error: {e}"
    if not text:
        return "Error: text or url required"
    raw = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    junk = {
        e
        for e in raw
        if any(
            x in e.lower() for x in ("example.", "sentry.io", "wixpress", ".png", ".jpg", ".gif", "noreply@localhost")
        )
    }
    good = sorted(raw - junk)
    if not good:
        return "No emails found"
    by_domain = {}
    for e in good:
        by_domain.setdefault(e.split("@")[1], []).append(e)
    return "\n".join(f"{d}: " + ", ".join(v[:8]) for d, v in sorted(by_domain.items()))
