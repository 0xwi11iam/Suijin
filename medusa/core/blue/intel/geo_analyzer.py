"""Geo analyzer — GeoIP lookup via free API."""
from __future__ import annotations
import json, urllib.request, socket


def get_geo(ip: str) -> dict:
    """Look up geographic location for an IP. Falls back gracefully."""
    # Skip local/private IPs
    if ip.startswith(("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.",
                       "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                       "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                       "172.29.", "172.30.", "172.31.", "0.", "169.254.")):
        return {"ip": ip, "country": "local", "city": "private_network", "is_private": True}

    try:
        # Try free ip-api.com (no key needed, 45 req/min rate limit)
        url = f"http://ip-api.com/json/{ip}?fields=country,city,org,isp"
        resp = urllib.request.urlopen(url, timeout=3)
        data = json.loads(resp.read())
        if data.get("status") != "fail":
            return {
                "ip": ip, "country": data.get("country", "unknown"),
                "city": data.get("city", "unknown"),
                "org": data.get("org", ""), "isp": data.get("isp", ""),
                "is_private": False,
            }
    except Exception:
        pass

    # Fallback: try hostname reverse lookup
    try:
        hostname = socket.gethostbyaddr(ip)[0]
        # Rough geo from TLD
        tld = hostname.split(".")[-1] if "." in hostname else ""
        country_map = {"cn": "China", "ru": "Russia", "ir": "Iran", "kp": "North Korea",
                       "br": "Brazil", "in": "India", "de": "Germany", "uk": "United Kingdom",
                       "jp": "Japan", "kr": "South Korea", "fr": "France", "au": "Australia"}
        return {"ip": ip, "country": country_map.get(tld, "unknown"), "city": "unknown",
                "hostname": hostname, "source": "reverse_dns", "is_private": False}
    except Exception:
        pass

    return {"ip": ip, "country": "unknown", "city": "unknown", "source": "none", "is_private": False}
