"""Threat Hunter — proactive scanning for missed attacks in traffic."""

from __future__ import annotations

import re


class ThreatHunter:
    """Proactively scans traffic for patterns the detectors might miss."""

    def __init__(self):
        self.scanned = 0
        self.discovered = 0

    def hunt(self, recent_requests: list) -> list:
        """Scan recent traffic for missed attack patterns."""
        findings = []
        for req in recent_requests:
            body = str(req.get("body", ""))
            path = req.get("path", "/")
            # Check for patterns the main detectors might miss
            if re.search(r"(?i)(\bexec\b.*\(|os\.system|subprocess\.)", body):
                findings.append({"type": "potential_rce", "path": path, "ip": req.get("ip")})
            if re.search(r"(?i)(\bwget\b|\bcurl\b).*(http|https)://", body):
                findings.append({"type": "potential_ssrf", "path": path, "ip": req.get("ip")})
            if re.search(r"(?i)(0x[0-9a-fA-F]{8,}|\\x[0-9a-fA-F]{2})", body):
                findings.append({"type": "potential_overflow", "path": path, "ip": req.get("ip")})
            self.scanned += 1
        self.discovered += len(findings)
        return findings

    def get_stats(self) -> dict:
        return {"scanned": self.scanned, "discovered": self.discovered}


def create_threat_hunter() -> ThreatHunter:
    return ThreatHunter()
