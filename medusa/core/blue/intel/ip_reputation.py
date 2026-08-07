"""IP reputation — abuseipdb, greylist integration."""
KNOWN_SCANNERS = ["shodan.io","censys.io","binaryedge.io"]
BLOCKED_RANGES = []

def check_reputation(ip: str) -> dict:
    return {"ip": ip, "known_scanner": any(s in ip for s in KNOWN_SCANNERS), "score": 0.0}
