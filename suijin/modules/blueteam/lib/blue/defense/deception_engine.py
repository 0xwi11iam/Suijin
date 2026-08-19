"""Deception engine — coordinate all deception tactics."""

from __future__ import annotations

from suijin.modules.blueteam.lib.blue.deception.honeypot_factory import generate_honeypot_response
from suijin.modules.blueteam.lib.blue.deception.shadow_redirect import redirect_to_shadow
from suijin.modules.blueteam.lib.blue.deception.time_sink import TimeSink
from suijin.modules.blueteam.lib.blue.errors import DeceptionError, ErrorSeverity, err, ok


def _score_deceive():
    from suijin.modules.platform.lib.constants import SCORE_DECEIVE as _v

    return _v


def _score_shadow():
    from suijin.modules.platform.lib.constants import SCORE_SHADOW as _v

    return _v


def _score_suspicious():
    from suijin.modules.platform.lib.constants import SCORE_SUSPICIOUS as _v

    return _v


def _tarpit_default_delay():
    from suijin.modules.platform.lib.constants import TARPIT_DEFAULT_DELAY as _v

    return _v


class DeceptionEngine:
    def __init__(self):
        self.time_sink = TimeSink()
        self.active_deceptions = {}

    def decide_response(self, attacker_id: str, request: dict, score: int) -> dict:
        try:
            if score >= _score_shadow():
                redirect_to_shadow(request.get("ip", ""))
                return ok({"action": "shadow_redirect"})
            if score >= _score_deceive():
                self.time_sink.tarpit(request.get("ip", ""))
                return ok({"action": "tarpit", "delay": _tarpit_default_delay()})
            if score >= _score_suspicious():
                return ok(
                    {"action": "honeypot", "response": generate_honeypot_response({"path": request.get("path", "/")})}
                )
            return ok({"action": "observe"})
        except Exception as e:
            import logging

            logging.getLogger("suijin").warning(f"Deception failed: {e}")
            return err(DeceptionError(f"Deception response failed: {e}", severity=ErrorSeverity.WARNING))
