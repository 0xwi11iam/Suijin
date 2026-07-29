"""crt.sh Certificate Transparency search."""
import requests
def crtsh_domain(domain):
    if not domain: return "Error: domain required"
    try:
        r = requests.get(f"https://crt.sh/?q=%25.{domain}&output=json", timeout=20)
        if r.status_code != 200: return f"crt.sh error {r.status_code}"
        names = sorted(set(e.get("name_value","") for e in r.json()))
        return "\n".join(names[:200]) or "(no results)"
    except Exception as e: return f"crt.sh error: {e}"