
import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


_RECURSORS = ("1.1.1.1", "8.8.8.8")


def _doh(name: str, rtype: str, server: str) -> list:
    r = _get(f"https://{server}/dns-query", params={"name": name, "type": rtype}, headers={"Accept": "application/dns-json"})
    r.raise_for_status()
    return [a.get("data") for a in r.json().get("Answer") or []]


def doh_resolve(name: str = "", type: str = "A") -> str:
    if not name:
        return "Error: name required"
    try:
        answers = _doh(name.strip(), (type or "A").upper(), _RECURSORS[0])
    except requests.RequestException as e:
        return f"Error: {e}"
    return f"{name} {(type or 'A').upper()} -> " + (", ".join(str(a) for a in answers) if answers else "no answer")


def dns_compare(name: str = "") -> str:
    if not name:
        return "Error: name required"
    results = {}
    for srv in _RECURSORS:
        try:
            results[srv] = _doh(name.strip(), "A", srv)
        except requests.RequestException as e:
            results[srv] = [f"error {type(e).__name__}"]
    same = list(results.values())[0] == results[_RECURSORS[1]]
    lines = [f"{srv}: {ans}" for srv, ans in results.items()]
    if not same:
        lines.append("SPLIT ANSWERS — GEO/CDN or split-horizon DNS (internal-name leak candidate)")
    return "\n".join(lines)
