"""DNS Zone Transfer — attempt AXFR on nameservers."""
import subprocess, re

def dns_zone_transfer(domain, nameserver=None):
    """Attempt DNS zone transfer (AXFR). Returns zone data or error."""
    cmd = ["dig", f"@{nameserver or '8.8.8.8'}", domain, "AXFR", "+short"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()
        if output and "Transfer failed" not in output and "connection refused" not in output.lower():
            records = [l for l in output.split("\n") if l.strip()]
            return f"Zone transfer successful! {len(records)} records:\n" + "\n".join(records[:100])
        return f"Zone transfer failed or empty: {result.stderr[:200] or output[:200]}"
    except Exception as e: return f"Error: {e}"

def dns_enum_nameservers(domain):
    """Enumerate nameservers for a domain."""
    try:
        result = subprocess.run(["dig", domain, "NS", "+short"], capture_output=True, text=True, timeout=10)
        ns = [l.strip().rstrip(".") for l in result.stdout.strip().split("\n") if l.strip()]
        return f"Nameservers: {', '.join(ns)}" if ns else "No nameservers found"
    except Exception as e: return f"Error: {e}"
