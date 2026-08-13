"""Honeypot — redirect attackers to decoy endpoints."""
from __future__ import annotations
def activate_honeypot(attacker_ip: str, endpoint: str) -> str:
    return f"Honeypot activated for {attacker_ip} on {endpoint}"

def deactivate_honeypot(attacker_ip: str) -> str:
    return f"Honeypot deactivated for {attacker_ip}"
