"""Counter-recon — passive OSINT on attacker infrastructure."""

from __future__ import annotations


def recon_attacker(ip: str) -> dict:
    import socket

    result = {"ip": ip}
    try:
        result["hostname"] = socket.gethostbyaddr(ip)[0]
    except Exception:
        # a missing PTR record is the NORMAL case for raw IPs — debug,
        # never warning (logging to stderr garbles the live console strip)
        import logging

        logging.getLogger("suijin").debug(f"Counter-recon: no PTR for {ip}")
        result["hostname"] = "unknown"
    return result
