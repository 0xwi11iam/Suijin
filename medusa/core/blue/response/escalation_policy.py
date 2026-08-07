"""Escalation policy — when to auto-block vs ask operator."""
def should_auto_block(score: int, config: dict) -> bool:
    if score >= config.get("scorer",{}).get("critical_threshold", 8):
        return config.get("response",{}).get("auto_block_critical", True)
    return False

def should_escalate_to_operator(score: int, attacker_profile: dict) -> bool:
    if score >= 9: return True
    if attacker_profile.get("total_requests", 0) > 100: return True
    return False
