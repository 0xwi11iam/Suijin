"""Deception engine — coordinate all deception tactics."""
from __future__ import annotations

from suijin.core.blue.deception.honeypot_factory import generate_honeypot_response
from suijin.core.blue.deception.shadow_redirect import redirect_to_shadow
from suijin.core.blue.deception.time_sink import TimeSink
from suijin.core.blue.errors import DeceptionError, ErrorSeverity, err, ok
from suijin.core.constants import SCORE_DECEIVE, SCORE_SHADOW, SCORE_SUSPICIOUS, TARPIT_DEFAULT_DELAY


class DeceptionEngine:
    def __init__(self):
        self.time_sink = TimeSink()
        self.active_deceptions = {}

    def decide_response(self, attacker_id: str, request: dict, score: int) -> dict:
        try:
            if score >= SCORE_SHADOW:
                redirect_to_shadow(request.get("ip",""))
                return ok({"action": "shadow_redirect"})
            if score >= SCORE_DECEIVE:
                self.time_sink.tarpit(request.get("ip",""))
                return ok({"action": "tarpit", "delay": TARPIT_DEFAULT_DELAY})
            if score >= SCORE_SUSPICIOUS:
                return ok({"action": "honeypot", "response": generate_honeypot_response({"path": request.get("path","/")})})
            return ok({"action": "observe"})
        except Exception as e:
            import logging
            logging.getLogger("suijin").warning(f"Deception failed: {e}")
            return err(DeceptionError(f"Deception response failed: {e}", severity=ErrorSeverity.WARNING))
