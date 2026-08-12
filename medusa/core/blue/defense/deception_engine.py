"""Deception engine — coordinate all deception tactics."""
from medusa.core.blue.deception.honeypot_factory import generate_honeypot_response
from medusa.core.blue.deception.time_sink import TimeSink
from medusa.core.blue.deception.shadow_redirect import redirect_to_shadow
from medusa.core.blue.errors import DeceptionError, ErrorSeverity, ok, err

class DeceptionEngine:
    def __init__(self):
        self.time_sink = TimeSink()
        self.active_deceptions = {}

    def decide_response(self, attacker_id: str, request: dict, score: int) -> dict:
        try:
            if score >= 9:
                redirect_to_shadow(request.get("ip",""))
                return ok({"action": "shadow_redirect"})
            if score >= 7:
                self.time_sink.tarpit(request.get("ip",""))
                return ok({"action": "tarpit", "delay": 8.0})
            if score >= 5:
                return ok({"action": "honeypot", "response": generate_honeypot_response({"path": request.get("path","/")})})
            return ok({"action": "observe"})
        except Exception as e:
            import logging; logging.getLogger("medusa").warning(f"Deception failed: {e}")
            return err(DeceptionError(f"Deception response failed: {e}", severity=ErrorSeverity.WARNING))
