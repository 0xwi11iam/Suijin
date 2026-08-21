import requests


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def http_alive(urls: str = "") -> str:
    if not urls:
        return "Error: urls required"
    batch = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()][:40]
    out = []
    for u in batch:
        if "://" not in u:
            u = "http://" + u
        try:
            r = requests.head(u, timeout=(3, 8), allow_redirects=True, headers={"User-Agent": _stealth_ua()})
            if r.status_code == 405:
                r = requests.get(u, timeout=(3, 8), headers={"User-Agent": _stealth_ua()}, stream=True)
            out.append(f"{r.status_code} {len(r.headers.get('content-length', '0')) or '?':>8}B  {u}")
        except requests.RequestException as e:
            out.append(f"DEAD  {type(e).__name__:>14}  {u}")
    return "\n".join(out)
