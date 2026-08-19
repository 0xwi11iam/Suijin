import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def proxy_check(proxy: str = "") -> str:
    if not proxy:
        return "Error: proxy required (scheme://host:port)"
    p = proxy.strip()
    if "://" not in p:
        p = "http://" + p
    try:
        direct = _get("https://api.ipify.org", timeout=(4, 10)).text
    except requests.RequestException as e:
        direct = f"(direct failed: {type(e).__name__})"
    try:
        via = requests.get("https://api.ipify.org", timeout=(4, 12), proxies={"http": p, "https": p}, headers=_UA).text
    except requests.RequestException as e:
        return f"direct egress: {direct}\nproxy {p}: FAILED ({type(e).__name__}) — dead, wrong scheme, or auth required"
    same = direct == via
    return f"direct egress: {direct}\nvia proxy:  {via}\n" + (
        "WARNING: egress identical — proxy is NOT masking your IP" if same else "proxy working — egress differs"
    )
