"""Tier 2 Analyst — validates threats, correlates across endpoints."""
from __future__ import annotations

import time


class Tier2Analyst:
    def __init__(self):
        self.correlated = 0
        self.validated = 0

    def validate(self, tier1_report: dict, kg_context: dict) -> dict:
        """Validate a tier-1 escalation with context from knowledge graph."""
        self.validated += 1
        attacker_hist = kg_context.get("attacker", {})
        is_repeat = attacker_hist.get("flags", 0) > 1
        return {
            "validated": True,
            "is_repeat_offender": is_repeat,
            "recommendation": "block" if is_repeat else "deceive",
            "time": time.time(),
        }

    def correlate(self, reports: list) -> dict:
        """Correlate multiple endpoint alerts to identify campaigns."""
        self.correlated += 1
        ips = set(r.get("ip", "") for r in reports if r.get("ip"))
        return {"correlated_ips": list(ips), "campaign_likely": len(ips) == 1}

    def get_stats(self) -> dict:
        return {"validated": self.validated, "correlated": self.correlated}


def create_tier2() -> Tier2Analyst:
    return Tier2Analyst()
