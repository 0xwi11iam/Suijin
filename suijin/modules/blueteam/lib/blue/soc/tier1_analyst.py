"""Tier 1 Analyst — first responder, initial triage of incoming alerts."""

from __future__ import annotations

import time


def _risk_high():
    from suijin.modules.platform.lib.constants import RISK_HIGH as _v

    return _v


class Tier1Analyst:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.triaged = 0
        self.escalated = 0

    def triage(self, request: dict, score: int) -> dict:
        self.triaged += 1
        if score >= _risk_high():
            self.escalated += 1
            return {"action": "escalate_to_tier2", "endpoint": self.endpoint, "score": score, "time": time.time()}
        return {"action": "log", "endpoint": self.endpoint, "score": score}

    def get_stats(self) -> dict:
        return {"endpoint": self.endpoint, "triaged": self.triaged, "escalated": self.escalated}


def create_tier1(endpoint: str) -> Tier1Analyst:
    return Tier1Analyst(endpoint)
