"""Watcher communication protocol."""
TIER1_ESCALATE = "TIER1_ESCALATE"
TIER2_VALIDATE = "TIER2_VALIDATE"
TIER2_DECEPTION = "TIER2_DECEPTION"
SOC_LEAD_DECISION = "SOC_LEAD_DECISION"
INCIDENT_DECLARE = "INCIDENT_DECLARE"
SHIFT_REALLOCATE = "SHIFT_REALLOCATE"

def create_message(msg_type: str, sender: str, payload: dict) -> dict:
    return {"type": msg_type, "sender": sender, "payload": payload}
