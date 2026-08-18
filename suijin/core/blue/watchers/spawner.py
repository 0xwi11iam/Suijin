"""Watcher spawner — create, assign, and manage endpoint watchers.

Each watcher is a lightweight monitoring agent assigned to a single endpoint.
It tracks request counts, anomaly rates, and reports health via heartbeat.
"""
from __future__ import annotations

import threading
import time


class EndpointWatcher:
    """Monitors a single endpoint for traffic anomalies and reports health."""

    def __init__(self, watcher_id: str, endpoint: dict):
        self.watcher_id = watcher_id
        self.endpoint = endpoint
        self.request_count = 0
        self.anomaly_count = 0
        self.last_request = 0.0
        self.last_heartbeat = time.time()
        self.status = "active"
        self._lock = threading.Lock()

    def record_request(self, is_anomaly: bool = False):
        with self._lock:
            self.request_count += 1
            if is_anomaly:
                self.anomaly_count += 1
            self.last_request = time.time()

    def heartbeat(self):
        self.last_heartbeat = time.time()

    def is_healthy(self) -> bool:
        return (time.time() - self.last_heartbeat) < 60

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "id": self.watcher_id,
                "endpoint": self.endpoint.get("path", "/"),
                "requests": self.request_count,
                "anomalies": self.anomaly_count,
                "anomaly_rate": self.anomaly_count / max(1, self.request_count),
                "last_request_ago": time.time() - self.last_request if self.last_request else -1,
                "healthy": self.is_healthy(),
            }


async def spawn_watchers(endpoints: list, config: dict) -> dict[str, EndpointWatcher]:
    """Spawn one watcher per endpoint. Returns dict of watcher_id -> EndpointWatcher."""
    tasks: dict[str, EndpointWatcher] = {}
    max_per = config.get("watchers", {}).get("max_per_endpoint", 3)
    for ep in endpoints:
        path = ep.get("path", "/")
        for i in range(min(1, max_per)):
            wid = f"watcher_{path.replace('/', '_')}_{i}"
            tasks[wid] = EndpointWatcher(wid, ep)
    return tasks
