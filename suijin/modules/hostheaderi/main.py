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


def host_header_inject(url: str = "", host_value: str = "evil.example") -> str:
    if not url:
        return "Error: url required"
    hv = (host_value or "evil.example").strip()
    try:
        import requests as _rq

        r = _rq.get(url.strip(), timeout=_T, headers={"Host": hv, **_UA}, allow_redirects=False)
    except requests.RequestException as e:
        return f"Error: {e}"
    hits = []
    if hv in r.text[:4000]:
        hits.append("Host REFLECTED in body (reset-link/cache poison candidate)")
    for h in ("Location", "Refresh", "X-Forwarded-Host", "Content-Location"):
        if hv in (r.headers.get(h) or ""):
            hits.append(f"Host reflected in {h}: {r.headers[h][:120]}")
    if not hits:
        return f"({r.status_code}) no reflection of Host: {hv} — likely safe on this endpoint"
    return "\n".join(hits) + "\ntarget reset/absolute-link endpoints for real impact"
