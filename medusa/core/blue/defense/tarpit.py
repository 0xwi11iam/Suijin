"""Tarpit — slow-loris style connection draining."""
import time, threading

class Tarpit:
    def __init__(self):
        self.active = {}
        self._lock = threading.Lock()
    def engage(self, ip: str, delay: float = 8.0):
        with self._lock: self.active[ip] = {"delay": delay, "count": 0, "started": time.time()}
    def is_engaged(self, ip: str) -> bool:
        return ip in self.active
    def process(self, ip: str):
        if ip in self.active:
            self.active[ip]["count"] += 1
            time.sleep(self.active[ip]["delay"])
    def disengage(self, ip: str):
        with self._lock:
            if ip in self.active: del self.active[ip]
