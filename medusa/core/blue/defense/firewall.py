"""Firewall — iptables/nftables rule management with IP validation."""
import subprocess, re, ipaddress

def _validate_ip(ip: str) -> str:
    """Validate and sanitize an IP address. Raises ValueError if invalid."""
    try:
        parsed = ipaddress.ip_address(ip.strip())
        return str(parsed)
    except ValueError:
        raise ValueError(f"Invalid IP address: {ip}")

def block_ip(ip: str) -> str:
    try:
        safe_ip = _validate_ip(ip)
        subprocess.run(["sudo","iptables","-A","INPUT","-s",safe_ip,"-j","DROP"], capture_output=True, timeout=5)
        return f"Blocked {safe_ip}"
    except ValueError as e:
        return f"Invalid IP: {e}"
    except Exception as e:
        import logging; logging.getLogger("medusa").warning(f"Firewall block failed for {ip}: {e}")
        return f"Failed to block {ip}: {e}"

def unblock_ip(ip: str) -> str:
    try:
        safe_ip = _validate_ip(ip)
        subprocess.run(["sudo","iptables","-D","INPUT","-s",safe_ip,"-j","DROP"], capture_output=True, timeout=5)
        return f"Unblocked {safe_ip}"
    except ValueError as e:
        return f"Invalid IP: {e}"
    except Exception as e:
        import logging; logging.getLogger("medusa").warning(f"Firewall unblock failed for {ip}: {e}")
        return f"Failed to unblock {ip}: {e}"

def list_blocks() -> list:
    try:
        r = subprocess.run(["sudo","iptables","-L","INPUT","-n"], capture_output=True, text=True, timeout=5)
        return [l for l in r.stdout.split("\n") if "DROP" in l]
    except Exception as e:
        import logging; logging.getLogger("medusa").warning(f"Firewall list failed: {e}")
        return []
