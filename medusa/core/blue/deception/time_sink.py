"""Time sink — waste attacker time with slow responses, redirects, loops.

NOTE: This module uses synchronous time.sleep(). It is designed for use in
threaded contexts (e.g., deception_engine.py). The blue team feed uses
file-based tarpit (/tmp/blue_tarpit.json) read by the proxy/Flask app,
which applies delays at the HTTP layer without blocking the event loop.
"""
from __future__ import annotations

import time


class TimeSink:
    def __init__(self):
        self.active_sinks = {}
    def tarpit(self, ip: str, delay_seconds: float = 8.0, max_requests: int = 100):
        self.active_sinks[ip] = {"type":"tarpit","delay":delay_seconds,"max":max_requests,"count":0,"started":time.time()}
    def cookie_stuff(self, ip: str):
        self.active_sinks[ip] = {"type":"cookie_stuff","count":0,"started":time.time()}
    def redirect_loop(self, ip: str):
        self.active_sinks[ip] = {"type":"redirect_loop","count":0,"started":time.time()}
    def should_sink(self, ip: str) -> bool:
        if ip not in self.active_sinks: return False
        sink = self.active_sinks[ip]
        if sink["type"] == "tarpit": return sink["count"] < sink["max"]
        return sink["count"] < 50
    def apply(self, ip: str) -> dict:
        if ip not in self.active_sinks: return {}
        sink = self.active_sinks[ip]
        sink["count"] += 1
        if sink["type"] == "tarpit":
            time.sleep(sink["delay"])
            return {"delay_applied": sink["delay"]}
        if sink["type"] == "cookie_stuff":
            return {"set_cookie": f"session_trap_{sink['count']}={'X'*min(sink['count']*100, 4000)}"}
        if sink["type"] == "redirect_loop":
            targets = ["/login","/auth","/verify","/login"]
            return {"redirect": targets[sink["count"] % len(targets)]}
        return {}
