"""Shodan OSINT integration."""
import os, requests
def _shodan_api(path, params=None):
    key = os.environ.get("SHODAN_API_KEY", "")
    if not key: return "Error: SHODAN_API_KEY env var not set"
    r = requests.get(f"https://api.shodan.io{path}", params={"key": key, **(params or {})}, timeout=15)
    if r.status_code == 200: return r.text[:4000]
    return f"Shodan error {r.status_code}: {r.text[:300]}"

def shodan_host(ip):
    if not ip: return "Error: ip required"
    return _shodan_api(f"/shodan/host/{ip}")

def shodan_domain(domain):
    if not domain: return "Error: domain required"
    return _shodan_api(f"/dns/domain/{domain}")