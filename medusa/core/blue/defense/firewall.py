"""Firewall — iptables/nftables rule management."""
import subprocess

def block_ip(ip: str) -> str:
    try:
        subprocess.run(["sudo","iptables","-A","INPUT","-s",ip,"-j","DROP"], capture_output=True, timeout=5)
        return f"Blocked {ip}"
    except: return f"Failed to block {ip} (sudo required)"

def unblock_ip(ip: str) -> str:
    try:
        subprocess.run(["sudo","iptables","-D","INPUT","-s",ip,"-j","DROP"], capture_output=True, timeout=5)
        return f"Unblocked {ip}"
    except: return f"Failed to unblock {ip}"

def list_blocks() -> list:
    try:
        r = subprocess.run(["sudo","iptables","-L","INPUT","-n"], capture_output=True, text=True, timeout=5)
        return [l for l in r.stdout.split("\n") if "DROP" in l]
    except: return []
