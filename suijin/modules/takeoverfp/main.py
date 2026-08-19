import requests

_FPRINTS = [
    ("github pages", r"\.(github\.io)\b", "There isn't a GitHub Pages site here"),
    ("heroku", r"\.herokuapp\.com\b", "No such app|Couldn't find that app"),
    ("aws s3", r"\.s3\.amazonaws\.com\b", "NoSuchBucket"),
    ("shopify", r"\.myshopify\.com\b", "Sorry, this shop is currently unavailable"),
    ("fastly", r"\.fastly\.net\b", "Fastly error: unknown domain"),
    ("pantheon", r"pantheon\.io\b", "The gods are wise"),
    ("zendesk", r"\.zendesk\.com\b", "this help center no longer exists"),
    ("surge.sh", r"\.surge\.sh\b", "project not found"),
    ("bitbucket", r"bitbucket\.org\b", "Repository not found"),
    ("cargo", r"cargocollective\.com\b", "404 Not Found"),
]
_T = (5, 15)


def takeover_fingerprint(hosts: str = "") -> str:
    if not hosts:
        return "Error: one or more hostnames required"
    import socket

    out = []
    for h in [x.strip() for x in hosts.split(",") if x.strip()][:20]:
        try:
            infos = socket.getaddrinfo(h, None)
            ips = {i[4][0] for i in infos}
            out.append(f"{h}: resolves ({', '.join(list(ips)[:3])}) — takeover unlikely while resolving")
            continue
        except socket.gaierror:
            pass
        # no A record: check CNAME via DoH
        try:
            r = requests.get(
                "https://1.1.1.1/dns-query",
                params={"name": h, "type": "CNAME"},
                headers={"Accept": "application/dns-json"},
                timeout=_T,
            )
            ans = r.json().get("Answer") or []
            cname = next((a["data"] for a in ans if a.get("type") == 5), None)
        except Exception:
            cname = None
        if not cname:
            out.append(f"{h}: no A/CNAME — dead name, nothing to take over")
            continue
        svc = next((name for name, pat, _ in _FPRINTS if __import__("re").search(pat, cname)), None)
        body_probe = ""
        if svc:
            for name, pat, sig in _FPRINTS:
                if name == svc:
                    try:
                        rr = requests.get(f"http://{h}", timeout=_T)
                        if sig.lower() in rr.text.lower():
                            body_probe = f" | service signature CONFIRMED ({sig[:40]}) — TAKEOVER CANDIDATE"
                    except requests.RequestException:
                        body_probe = " | service unreachable (consistent with dangling)"
                    break
        out.append(f"{h}: CNAME -> {cname} ({svc or 'unknown service'}){body_probe}")
    return "\n".join(out)
