import requests

_T = (5, 20)
_UA = {"User-Agent": "Mozilla/5.0 (suijin recon)"}


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_UA, **kw)


def crtsh_subdomains(domain: str = "") -> str:
    if not domain:
        return "Error: domain required"
    try:
        r = _get("https://crt.sh/", params={"q": f"%.{domain.strip()}", "output": "json"})
        if r.status_code != 200:
            return f"crt.sh returned {r.status_code} (rate-limited? retry)"
        import json

        rows = r.json()
    except (requests.RequestException, ValueError) as e:
        return f"Error: {e}"
    names = set()
    for row in rows[:2000]:
        for n in (row.get("name_value") or "").split("\n"):
            n = n.strip().lstrip("*.")
            if n.endswith("." + domain.strip()) and n:
                names.add(n)
    if not names:
        return f"No CT-log subdomains for {domain}"
    interesting = [
        n
        for n in sorted(names)
        if any(x in n for x in ("dev", "stage", "test", "admin", "api", "internal", "vpn", "mail", "jenkins", "git"))
    ]
    return (
        f"{len(names)} subdomains via CT logs\n"
        + ("high-value:\n  " + "\n  ".join(interesting[:30]) + "\n" if interesting else "")
        + "all:\n  "
        + "\n  ".join(sorted(names)[:120])
    )
