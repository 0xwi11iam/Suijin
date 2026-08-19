import requests


def trace_redirects(url: str = "") -> str:
    if not url:
        return "Error: url required"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    hops = []
    current = url
    try:
        for _ in range(10):
            r = requests.get(
                current, allow_redirects=False, timeout=(5, 15), headers={"User-Agent": "Mozilla/5.0 (suijin recon)"}
            )
            hops.append(f"{r.status_code} {current}")
            loc = r.headers.get("Location")
            if not loc or not (300 <= r.status_code < 400):
                hops.append(f"FINAL {r.status_code} ({len(r.content):,} bytes, server: {r.headers.get('server', '?')})")
                break
            from urllib.parse import urljoin

            current = urljoin(current, loc)
        else:
            hops.append("STOPPED: exceeded 10 hops (redirect loop?)")
    except requests.RequestException as e:
        hops.append(f"ERROR at {current}: {e}")
    notes = []
    if any("http://" in h and url.startswith("https://") for h in hops):
        notes.append("TLS downgrade in chain")
    if len(hops) >= 11:
        notes.append("long/looping chain — open-redirect abuse candidate")
    return "\n".join(hops + (["notes: " + "; ".join(notes)] if notes else []))
