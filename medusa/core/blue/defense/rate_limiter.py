"""Dynamic rate limiter — tighten limits on attacked endpoints."""
import time
from collections import defaultdict

class DynamicRateLimiter:
    def __init__(self):
        self.limits = defaultdict(lambda: {"rpm": 60, "burst": 10})
        self._requests = defaultdict(list)
    def tighten(self, endpoint: str, factor: float = 0.5):
        old = self.limits[endpoint]["rpm"]
        self.limits[endpoint]["rpm"] = max(5, int(old * factor))
    def relax(self, endpoint: str):
        self.limits[endpoint]["rpm"] = min(60, int(self.limits[endpoint]["rpm"] * 1.5))
    def is_limited(self, endpoint: str) -> bool:
        now = time.time()
        limit = self.limits[endpoint]["rpm"]
        self._requests[endpoint] = [t for t in self._requests[endpoint] if now - t < 60]
        self._requests[endpoint].append(now)
        return len(self._requests[endpoint]) > limit
