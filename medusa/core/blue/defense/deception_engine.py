"""Deception engine — coordinate all deception tactics."""
from medusa.core.blue.deception.honeypot_factory import generate_honeypot_response
from medusa.core.blue.deception.time_sink import TimeSink
from medusa.core.blue.deception.shadow_redirect import redirect_to_shadow

class DeceptionEngine:
    def __init__(self):
        self.time_sink = TimeSink()
        self.active_deceptions = {}
    def decide_response(self, attacker_id: str, request: dict, score: int) -> dict:
        if score >= 9:
            redirect_to_shadow(request.get("ip",""))
            return {"action": "shadow_redirect"}
        if score >= 7:
            self.time_sink.tarpit(request.get("ip",""))
            return {"action": "tarpit", "delay": 8.0}
        if score >= 5:
            return {"action": "honeypot", "response": generate_honeypot_response({"path": request.get("path","/")})}
        return {"action": "observe"}
