import requests

_T = (5, 20)


def _ua() -> dict:
    """Headers with the stealth identity, resolved lazily (importing at
    module scope ran before _stealth_ua existed — loader crash)."""
    return {"User-Agent": _stealth_ua()}


def _stealth_ua() -> str:
    """Stealth identity (lazy import, fallback to a realistic browser)."""
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _get(url, **kw):
    return requests.get(url, timeout=_T, headers=_ua(), **kw)


def sourcemap_check(urls: str = "") -> str:
    if not urls:
        return "Error: one or more .js URLs required"
    found = []
    for u in [x.strip() for x in urls.split(",") if x.strip()][:10]:
        if not u.endswith(".js"):
            u += ".js"
        for cand in (u + ".map", u[:-3] + ".map"):
            try:
                r = _get(cand)
                if r.status_code == 200 and (
                    "sourceMappingURL" in r.text[:200] or "sources" in r.text[:400] or r.text.lstrip().startswith("{")
                ):
                    n = r.text.count('"sourceRoot"') + len(re.findall(r'"sources"', r.text[:2000]))
                    found.append(f"SOURCE MAP EXPOSED {cand} ({len(r.content):,}B)")
                    break
            except requests.RequestException:
                continue
    return "\n".join(found) if found else "No exposed source maps on the given bundles."


import re


def _stealth_ua() -> str:
    try:
        from suijin.modules.platform.lib.stealth import user_agent

        return user_agent()
    except Exception:  # standalone fallback
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
