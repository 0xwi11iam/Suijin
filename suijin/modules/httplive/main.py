import requests


def http_alive(urls: str = "") -> str:
    if not urls:
        return "Error: urls required"
    batch = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()][:40]
    out = []
    for u in batch:
        if "://" not in u:
            u = "http://" + u
        try:
            r = requests.head(u, timeout=(3, 8), allow_redirects=True, headers={"User-Agent": "suijin-alive"})
            if r.status_code == 405:
                r = requests.get(u, timeout=(3, 8), headers={"User-Agent": "suijin-alive"}, stream=True)
            out.append(f"{r.status_code} {len(r.headers.get('content-length', '0')) or '?':>8}B  {u}")
        except requests.RequestException as e:
            out.append(f"DEAD  {type(e).__name__:>14}  {u}")
    return "\n".join(out)
