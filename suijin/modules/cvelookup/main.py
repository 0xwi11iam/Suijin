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


def cve_search_nvd(keyword: str = "", api_key: str = "", limit: int = 10) -> str:
    if not keyword:
        return "Error: keyword required"
    hdrs = dict(_UA)
    if api_key:
        hdrs["apiKey"] = api_key.strip()
    try:
        r = _get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"keywordSearch": keyword.strip(), "resultsPerPage": min(int(limit or 10), 20)},
            headers=hdrs,
        )
    except requests.RequestException as e:
        return f"Error: {e}"
    if r.status_code == 403:
        return "rate-limited (NVD allows ~5 req/30s without a key; get one at nvd.nist.gov/developers)"
    try:
        vulns = r.json().get("vulnerabilities") or []
    except ValueError:
        return f"non-JSON response ({r.status_code})"
    out = [f"{len(vulns)} CVEs for '{keyword}'"]
    for v in vulns[:10]:
        cve = v.get("cve") or {}
        desc = next((d.get("value", "") for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")[:140]
        sev = ""
        for m in cve.get("metrics") or {}.values():
            for x in m:
                sev = f" {x.get('cvssData', {}).get('baseSeverity', '')} {x.get('cvssData', {}).get('baseScore', '')}".strip()
                break
            break
        out.append(f"  {cve.get('id')}{(' [' + sev + ']') if sev else ''}: {desc}")
    return "\n".join(out)
