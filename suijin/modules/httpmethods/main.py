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


def method_scan(url: str = "") -> str:
    if not url:
        return "Error: url required"
    rows = []
    for m in ("OPTIONS", "TRACE", "PUT", "DELETE", "PATCH", "DEBUG"):
        try:
            import requests as _rq

            r = _rq.request(m, url.strip(), timeout=_T, headers={**_UA, "Allow": m}, allow_redirects=False)
            allow = r.headers.get("Allow") or r.headers.get("allow") or ""
            note = ""
            if m == "TRACE" and "TRACE" in r.text[:200].upper():
                note = "XST possible (TRACE reflected)"
            if m == "PUT" and r.status_code in (200, 201, 204):
                note = "PUT accepted — arbitrary upload candidate"
            if m == "DEBUG" and r.status_code < 400:
                note = "DEBUG verb live (Spring/Actuator style)"
            rows.append(f"{m:8} {r.status_code} allow={allow[:60] or '-'}{(' ' + note) if note else ''}")
        except requests.RequestException as e:
            rows.append(f"{m:8} err {type(e).__name__}")
    return "\n".join(rows)
