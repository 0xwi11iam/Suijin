import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


_T = (5, 20)
_UA = {"User-Agent": "_stealth_ua()"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def cache_probe(url: str = "") -> str:
    if not url:
        return "Error: url required"
    import hashlib

    out = []
    try:
        r1 = _get(url.strip())
        r2 = _get(url.strip())
    except requests.RequestException as e:
        return f"Error: {e}"
    cache_hdrs = {
        k: v
        for k, v in r1.headers.items()
        if k.lower()
        in ("x-cache", "cf-cache-status", "age", "x-varnish", "x-drupal-cache", "x-proxy-cache", "via", "etag")
    }
    if cache_hdrs:
        out.append("cache headers: " + "; ".join(f"{k}={v[:40]}" for k, v in cache_hdrs.items()))
    same_body = hashlib.sha256(r1.content).hexdigest() == hashlib.sha256(r2.content).hexdigest()
    cached = r1.headers.get("age") or r2.headers.get("age") or r1.headers.get("x-cache") == "HIT"
    if same_body and cached:
        out.append(
            "body stable + age/HIT present: cached layer exists — test unkeyed inputs (headers like X-Forwarded-Host) for poisoning"
        )
    # unkeyed reflection
    try:
        import requests as _rq

        rx = _rq.get(url.strip(), timeout=_T, headers={**_UA, "X-Forwarded-Host": "canary.example"})
        if "canary.example" in rx.text[:4000]:
            out.append("X-Forwarded-Host REFLECTED and unkeyed — cache poisoning candidate")
    except requests.RequestException:
        pass
    return "\n".join(out) or "no cache layer detected / bodies vary"
