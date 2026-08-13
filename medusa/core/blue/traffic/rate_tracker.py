"""Rate tracker — per-IP, per-endpoint sliding window tracking."""
from __future__ import annotations
import time, threading
from collections import defaultdict

class RateTracker:
    def __init__(self, window_seconds: int = 60):
        self.window = window_seconds
        self._ips = defaultdict(list)
        self._endpoints = defaultdict(list)
        self._lock = threading.Lock()
    def record(self, ip: str, endpoint: str):
        now = time.time()
        with self._lock:
            self._ips[ip] = [t for t in self._ips[ip] if now - t < self.window] + [now]
            self._endpoints[endpoint] = [t for t in self._endpoints[endpoint] if now - t < self.window] + [now]
    def ip_rate(self, ip: str) -> int:
        now = time.time()
        with self._lock:
            self._ips[ip] = [t for t in self._ips[ip] if now - t < self.window]
            return len(self._ips[ip])
    def endpoint_rate(self, endpoint: str) -> int:
        now = time.time()
        with self._lock:
            self._endpoints[endpoint] = [t for t in self._endpoints[endpoint] if now - t < self.window]
            return len(self._endpoints[endpoint])
    def is_flooding(self, ip: str, threshold: int = 50) -> bool:
        return self.ip_rate(ip) > threshold
