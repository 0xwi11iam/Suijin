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


def ip_abuse_check(ip: str = "", api_key: str = "") -> str:
    if not ip:
        return "Error: ip required"
    if not api_key:
        return (
            "Error: AbuseIPDB requires a free API key (https://www.abuseipdb.com/account/api) — "
            "pass api_key= or use asn_lookup (no key) for ownership intel"
        )
    try:
        r = _get(
            "https://api.abuseipdb.com/api/v2/check",
            params={"ipAddress": ip.strip(), "maxAgeInDays": 90},
            headers={"Key": api_key.strip(), "Accept": "application/json"},
        )
    except requests.RequestException as e:
        return f"Error: {e}"
    if r.status_code != 200:
        return f"API returned {r.status_code}: {r.text[:200]}"
    d = r.json().get("data") or {}
    return (
        f"{d.get('ipAddress')}: abuse score {d.get('abuseConfidenceScore')}/100, "
        f"{d.get('totalReports')} reports, usage: {d.get('usageType', '?')}, isp: {d.get('isp', '?')}"
    )
