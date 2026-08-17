"""Live traffic capture — mitmproxy/tcpdump integration."""
from __future__ import annotations

from medusa.core.constants import PROXY_DEFAULT_PORT


class TrafficCapture:
    def __init__(self, port: int = PROXY_DEFAULT_PORT):
        self.port = port
        self._buffer = []
        self._running = False
    def start(self):
        self._running = True
    def stop(self):
        self._running = False
    def get_recent(self, count: int = 100) -> list:
        return self._buffer[-count:]
    def add(self, request: dict):
        self._buffer.append(request)
        if len(self._buffer) > 10000:
            self._buffer = self._buffer[-5000:]
