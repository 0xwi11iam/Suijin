"""Psychological ops — mess with attacker's head."""
from __future__ import annotations
import random

def gaslight_attacker(attacker_id: str, attempt_count: int) -> dict:
    responses = [
        ({"status": 200, "body": '{"error":"Account locked. Contact support@corp.com to unlock."}'}, "fake_lockout"),
        ({"status": 200, "body": '{"error":"Password expired. Reset link sent."}'}, "fake_expiry"),
        ({"status": 200, "body": '{"error":"2FA code sent to registered device."}'}, "fake_2fa"),
        ({"status": 200, "body": '{"error":"Account suspended due to suspicious activity. Reference: SEC-'+str(random.randint(10000,99999))+'."}'}, "fake_suspension"),
        ({"status": 500, "body": "<h1>500 Internal Server Error</h1><pre>Stack trace shows database credentials</pre>"}, "fake_crash"),
    ]
    if attempt_count > 10:
        return ({"status": 200, "body": "OK"}, "reset")
    idx = min(attempt_count - 1, len(responses) - 1)
    return responses[idx]
