"""Real-time metrics — traffic rate, blocks, watcher health."""
from __future__ import annotations
import time
from collections import defaultdict

class BlueMetrics:
    def __init__(self):
        self.request_count = 0
        self.block_count = 0
        self.deception_count = 0
        self.hotfix_count = 0
        self.cost_usd = 0.0
        self.requests_per_minute = 0
        self._minute_requests = []
        self.started_at = time.time()
    def record_request(self): self.request_count += 1
    def record_block(self): self.block_count += 1
    def record_deception(self): self.deception_count += 1
    def record_hotfix(self): self.hotfix_count += 1
    def add_cost(self, usd: float): self.cost_usd += usd
    def snapshot(self) -> dict:
        now = time.time()
        self._minute_requests = [t for t in self._minute_requests if now - t < 60]
        self.requests_per_minute = len(self._minute_requests)
        return {"requests": self.request_count, "blocks": self.block_count, "deceptions": self.deception_count,
                "hotfixes": self.hotfix_count, "cost": self.cost_usd, "rpm": self.requests_per_minute,
                "uptime_minutes": (now - self.started_at) / 60}
