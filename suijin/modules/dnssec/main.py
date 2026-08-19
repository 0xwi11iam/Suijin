import requests

_SELECTORS = ("default", "google", "selector1", "selector2", "k1", "mail", "dkim", "s1", "s2", "zoho")


def _txt(name: str) -> list:
    try:
        r = requests.get(
            "https://1.1.1.1/dns-query",
            params={"name": name, "type": "TXT"},
            headers={"Accept": "application/dns-json"},
            timeout=(4, 10),
        )
        return [a.get("data", "").strip('"') for a in r.json().get("Answer") or []]
    except (requests.RequestException, ValueError):
        return []


def email_security_records(domain: str = "") -> str:
    if not domain:
        return "Error: domain required"
    d = domain.strip().lower().strip(".")
    spf = [t for t in _txt(d) if t.lower().startswith("v=spf1")]
    dmarc = [t for t in _txt(f"_dmarc.{d}") if t.lower().startswith("v=dmarc1")]
    dkim_found = []
    for sel in _SELECTORS:
        recs = _txt(f"{sel}._domainkey.{d}")
        if recs:
            dkim_found.append(f"{sel}: {recs[0][:80]}")
    out = [f"SPF:   {spf[0] if spf else 'NOT FOUND'}"]
    out.append(f"DMARC: {dmarc[0] if dmarc else 'NOT FOUND'}")
    out.append(f"DKIM:  {'; '.join(dkim_found) if dkim_found else 'none of the common selectors found'}")
    if not spf:
        out.append("!! no SPF — domain can be spoofed for sending")
    if not dmarc:
        out.append("!! no DMARC — receivers have no policy guidance")
    elif "p=none" in dmarc[0].lower():
        out.append("!! DMARC p=none — monitor-only, no enforcement")
    return "\n".join(out)


def spf_audit(domain: str = "") -> str:
    if not domain:
        return "Error: domain required"
    d = domain.strip().lower().strip(".")
    spf = next((t for t in _txt(d) if t.lower().startswith("v=spf1")), None)
    if not spf:
        return f"No SPF record for {d}"
    tokens = spf.split()[1:]
    includes = [t[8:] for t in tokens if t.startswith("include:")]
    ips = [t for t in tokens if t.startswith(("ip4:", "ip6:"))]
    allmech = next((t for t in tokens if t in ("+all", "-all", "~all", "?all", "all")), None)
    lookup_depth = 0
    lines = [f"SPF for {d}: {len(tokens)} mechanisms, {len(includes)} includes, {len(ips)} ip literals"]
    if includes:
        lines.append("  includes: " + ", ".join(includes[:10]))
    if len(includes) > 10:
        lines.append("  !! >10 includes risks exceeding the 10-lookup DNS limit")
    if allmech:
        verdict = {
            "-all": "HARD FAIL (good)",
            "~all": "soft fail (weak)",
            "?all": "neutral (weak)",
            "+all": "PASS ALL (spoofable!)",
            "all": "PASS ALL (spoofable!)",
        }
        lines.append(f"  all policy: {allmech} -> {verdict.get(allmech, '?')}")
    else:
        lines.append("  !! no 'all' mechanism — defaults to neutral")
    return "\n".join(lines)
