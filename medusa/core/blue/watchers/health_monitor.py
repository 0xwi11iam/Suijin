"""Watcher health monitor — heartbeat checks, auto-respawn."""
from __future__ import annotations
import asyncio, time

class HealthMonitor:
    def __init__(self):
        self.watchers = {}
        self._last_heartbeat = {}
    def register(self, watcher_id: str):
        self.watchers[watcher_id] = "healthy"
        self._last_heartbeat[watcher_id] = time.time()
    def heartbeat(self, watcher_id: str):
        self._last_heartbeat[watcher_id] = time.time()
    def get_unhealthy(self, timeout: float = 30.0) -> list:
        now = time.time()
        return [wid for wid, last in self._last_heartbeat.items() if now - last > timeout]
