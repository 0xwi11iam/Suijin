
import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


_CANARY = "https://example.org/safe-canary"


def open_redirect_check(url: str = "", param: str = "") -> str:
    if not url:
        return "Error: url required (the endpoint with its redirect param, e.g. /login?next=/home)"
    import urllib.parse
    parts = urllib.parse.urlsplit(url.strip())
    qs = urllib.parse.parse_qs(parts.query)
    if not param:
        cands = [k for k in qs if k.lower() in ("next", "url", "redirect", "return", "returnurl", "continue", "dest", "goto", "r", "u", "target")]
        param = cands[0] if cands else (list(qs)[0] if qs else "")
    if not param:
        return "could not find a candidate param; pass param=<name>"
    new_q = {k: v[0] for k, v in qs.items()}
    new_q[param] = _CANARY
    test = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(new_q), ""))
    try:
        r = _get(test, allow_redirects=False)
    except requests.RequestException as e:
        return f"Error: {e}"
    loc = r.headers.get("Location", "")
    if "example.org" in loc:
        return f"OPEN REDIRECT: {param}={_CANARY} -> 30x Location={loc}\nURL: {test}"
    if 300 <= r.status_code < 400:
        return f"redirects but not to canary ({loc[:100]}) — param validated or different flow"
    return f"no redirect on this request ({r.status_code}) — check where the param is consumed"
