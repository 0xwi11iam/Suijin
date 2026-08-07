"""Infrastructure mapper — trace attacker IPs, map proxies/VPNs/botnets."""
import socket

def reverse_dns(ip: str) -> str:
    try: return socket.gethostbyaddr(ip)[0]
    except: return "unknown"

def is_vpn_or_proxy(ip: str) -> bool:
    vpn_ranges = ["104.28.","104.29.","172.64."]
    return any(ip.startswith(r) for r in vpn_ranges)
