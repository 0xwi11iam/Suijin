import requests


def wayback_urls(domain: str = "", limit: int = 200) -> str:
    if not domain:
        return "Error: domain required"
    try:
        limit = min(int(limit or 200), 1000)
        r = requests.get(
            "http://web.archive.org/cdx/search/cdx",
            params={
                "url": f"{domain}/*",
                "output": "text",
                "fl": "original",
                "collapse": "urlkey",
                "limit": str(limit),
            },
            timeout=(5, 25),
        )
        if r.status_code != 200:
            return f"CDX returned {r.status_code}"
        urls = sorted(set(r.text.split()))
        if not urls:
            return f"No archived URLs for {domain}"
        gold = [
            u
            for u in urls
            if any(
                x in u.lower()
                for x in (
                    ".bak",
                    ".sql",
                    ".env",
                    ".git",
                    "admin",
                    "backup",
                    "config",
                    "test.",
                    "old.",
                    "passwd",
                    "token",
                    "api?key",
                    "secret",
                )
            )
        ]
        out = [f"{len(urls)} archived URLs (cap {limit})"]
        if gold:
            out.append("high-value survivors:\n  " + "\n  ".join(gold[:40]))
        out.append("all:\n  " + "\n  ".join(urls[:150]))
        return "\n".join(out)
    except requests.RequestException as e:
        return f"Error: {e}"
