"""Log correlator — cross-reference across endpoints."""
from __future__ import annotations


def correlate_sessions(ip: str, log_paths: list) -> list:
    results = []
    from medusa.core.blue.forensics.log_reader import filter_by_ip
    for lp in log_paths:
        lines = filter_by_ip(lp, ip)
        if lines and lines != "No matches":
            results.append({"log": lp, "entries": lines[:500]})
    return results
