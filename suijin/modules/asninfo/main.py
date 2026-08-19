import requests


def asn_lookup(ip: str = "") -> str:
    if not ip:
        return "Error: ip required"
    try:
        r = requests.get(f"https://api.bgpview.io/ip/{ip.strip()}", timeout=(5, 20))
        r.raise_for_status()
        d = r.json().get("data") or {}
        asn = d.get("asn") or {}
        prefixes = [p.get("prefix") for p in (d.get("prefixes") or [])][:8]
        out = [
            f"IP {ip}: {d.get('description') or d.get('name', '?')}",
            f"ASN: {asn.get('asn', '?')} {asn.get('name', '')} {asn.get('description', '')}".rstrip(),
        ]
        if prefixes:
            out.append("prefixes: " + ", ".join(p for p in prefixes if p))
        if d.get("rir_allocation"):
            ra = d["rir_allocation"]
            out.append(f"RIR: {ra.get('rir_name')} {ra.get('prefix')}")
        if d.get("country_code"):
            out.append(f"country: {d['country_code']}")
        return "\n".join(out)
    except requests.RequestException as e:
        return f"Error: {e}"
    except (KeyError, ValueError) as e:
        return f"Error parsing response: {e}"
