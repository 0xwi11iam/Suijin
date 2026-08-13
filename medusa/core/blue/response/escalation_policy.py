"""Escalation policy — when to auto-block vs ask operator."""
from __future__ import annotations
from medusa.core.constants import SCORE_CRITICAL, SCORE_SHADOW

def should_auto_block(score: int, config: dict) -> bool:
    if score >= config.get("scorer",{}).get("critical_threshold", SCORE_CRITICAL):
        return config.get("response",{}).get("auto_block_critical", True)
    return False

def should_escalate_to_operator(score: int, attacker_profile: dict) -> bool:
    if score >= SCORE_SHADOW: return True
    if attacker_profile.get("total_requests", 0) > 100: return True
    return False
