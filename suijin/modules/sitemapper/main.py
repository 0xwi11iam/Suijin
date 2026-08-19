import re


def parse_sitemap(content: str = "") -> str:
    if not content:
        return "Error: sitemap content required"
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", content)
    if not locs:
        return "No <loc> entries found (not a sitemap?)"
    uniq = sorted(set(locs))
    idx = [u for u in uniq if u.endswith(".xml") or u.endswith(".xml.gz")]
    pages = [u for u in uniq if u not in idx]
    out = [f"{len(pages)} pages, {len(idx)} nested sitemaps"]
    if idx:
        out.append("nested sitemaps (fetch + re-parse):\n  " + "\n  ".join(idx))
    out.append("pages:\n  " + "\n  ".join(pages))
    return "\n".join(out)
