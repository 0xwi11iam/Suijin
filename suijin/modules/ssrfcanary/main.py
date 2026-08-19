
import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def ssrf_canary(token: str = "") -> str:
    import uuid
    t = (token or uuid.uuid4().hex[:10]).strip()
    return (
        f"canary token: {t}\n"
        f"  webhook:   https://{t}.oast.example.com/            (swap in your burp-collaborator/webhook.site host)\n"
        f"  unique-img https://webhook.site/{t}\nhow to use: inject the URL into every param that might be fetched server-side; "
        "watch the callback service for hits = confirmed SSRF"
    )


def ssrf_blind_probe(url: str = "") -> str:
    if not url:
        return "Error: url required (with a param value you control pointing at a BLACKHOLE ip:port)"
    try:
        import time
        t0 = time.perf_counter()
        r = _get(url.strip(), timeout=(5, 35))
        dt = time.perf_counter() - t0
        if dt > 5:
            return f"took {dt:.1f}s — server LIKELY waited on your target (blind SSRF candidate); confirm with a canary"
        return f"returned in {dt:.1f}s ({r.status_code}) — no obvious hang"
    except requests.RequestException as e:
        if "ReadTimeout" in type(e).__name__:
            return "client timeout — if your injected host is unroutable, the server may still be hanging on it (SSRF candidate)"
        return f"Error: {e}"
