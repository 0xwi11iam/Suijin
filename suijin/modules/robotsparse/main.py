import re
import urllib.parse


def analyze_robots(content: str = "", base_url: str = "") -> str:
    if not content:
        return "Error: robots.txt content required"
    disallows = []
    sitemaps = []
    for line in content.splitlines():
        line = line.split("#")[0].strip()
        if line.lower().startswith("disallow:"):
            p = line.split(":", 1)[1].strip()
            if p and p != "/":
                disallows.append(p)
        elif line.lower().startswith("sitemap:"):
            sitemaps.append(line.split(":", 1)[1].strip())
    juicy = [
        p
        for p in disallows
        if re.search(
            r"(admin|backup|config|secret|private|test|dev|internal|api|\.git|\.env|upload|dashboard|cpanel|phpmyadmin|wp-)",
            p,
            re.I,
        )
    ]
    out = [f"{len(disallows)} disallow rules; {len(sitemaps)} sitemaps"]
    if sitemaps:
        out.append("sitemaps:\n  " + "\n  ".join(sitemaps))
    if juicy:
        out.append("HIGH-VALUE (hidden but indexed-by-rule):\n  " + "\n  ".join(juicy))
    if base_url:
        origin = base_url if "://" in base_url else f"http://{base_url}"
        probes = juicy or disallows[:20]
        out.append(
            "probe list (absolute):\n  " + "\n  ".join(urllib.parse.urljoin(origin + "/", p.strip("/")) for p in probes)
        )
    return "\n".join(out)
