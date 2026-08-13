"""Infrastructure mapper — trace attacker IPs, map proxies/VPNs/botnets."""
from __future__ import annotations
import socket

def reverse_dns(ip: str) -> str:
    try: return socket.gethostbyaddr(ip)[0]
    except Exception:
        import logging; logging.getLogger("medusa").warning("Infrastructure mapping failed", exc_info=True)
        return "unknown"

def is_vpn_or_proxy(ip: str) -> bool:
    vpn_ranges = ["104.28.","104.29.","172.64."]
    return any(ip.startswith(r) for r in vpn_ranges)
