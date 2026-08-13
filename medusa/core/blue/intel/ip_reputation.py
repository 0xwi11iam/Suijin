"""IP reputation — threat intelligence scoring."""
from __future__ import annotations
import os, json, time

KNOWN_SCANNERS = ["shodan.io", "censys.io", "binaryedge.io", "onyphe.io",
                  "stretchoid.com", "shadowserver.org", "internet-census.org"]
BLOCKED_RANGES: list[str] = []

# In-memory reputation cache (resets each session)
_reputation_cache: dict[str, dict] = {}


def check_reputation(ip: str) -> dict:
    """Score an IP's reputation based on multiple signals.

    Checks: known scanner networks, private/loopback, repeated attacks in
    this session (from knowledge graph), and reverse DNS patterns.
    Returns score 0.0-1.0 where 1.0 = definitely malicious.
    """
    if ip in _reputation_cache:
        cached = _reputation_cache[ip]
        if time.time() - cached.get("checked_at", 0) < 300:  # 5-min cache
            return cached

    score = 0.0
    reasons = []

    # Check private/loopback — not inherently malicious but suspicious in some contexts
    if ip.startswith(("127.", "10.", "192.168.", "172.16.")):
        reasons.append("private_ip")

    # Check against known scanner networks via reverse DNS
    try:
        import socket
        hostname = socket.gethostbyaddr(ip)[0].lower()
        for scanner in KNOWN_SCANNERS:
            if scanner in hostname:
                score += 0.4
                reasons.append(f"known_scanner:{scanner}")
                break
    except Exception:
        pass

    # Check knowledge graph for repeat offenses
    try:
        from medusa.core.blue.knowledge_graph import get_kg
        kg = get_kg()
        hist = kg.get_attacker_history(ip)
        flags = hist.get("total_flags", 0)
        if flags >= 3:
            score += 0.6
            reasons.append(f"repeat_offender:{flags}_flags")
        elif flags >= 1:
            score += 0.3
            reasons.append(f"previous_attacker:{flags}_flags")
    except Exception:
        pass

    result = {
        "ip": ip, "score": min(1.0, score),
        "known_scanner": any("scanner" in r for r in reasons),
        "reasons": reasons, "checked_at": time.time(),
    }
    _reputation_cache[ip] = result
    return result
