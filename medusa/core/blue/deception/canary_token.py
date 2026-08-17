"""Canary tokens — tracking tokens that phone home when used."""
from __future__ import annotations

import hashlib
import time


class CanaryToken:
    def __init__(self, token_type: str, value: str):
        self.token_type = token_type
        self.value = value
        self.hash = hashlib.sha256(value.encode()).hexdigest()[:16]
        self.created_at = time.time()
        self.triggered = False
        self.triggered_at = None
        self.triggered_by_ip = None
    def trigger(self, ip: str):
        self.triggered = True
        self.triggered_at = time.time()
        self.triggered_by_ip = ip
    def to_dict(self) -> dict:
        return {"type": self.token_type, "hash": self.hash, "triggered": self.triggered}

_canary_store = {}

def deploy_canary(token_type: str, value: str) -> CanaryToken:
    token = CanaryToken(token_type, value)
    _canary_store[token.hash] = token
    return token

def check_canary(value: str) -> CanaryToken | None:
    h = hashlib.sha256(value.encode()).hexdigest()[:16]
    return _canary_store.get(h)
