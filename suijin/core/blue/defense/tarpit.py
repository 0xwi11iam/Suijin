"""Tarpit — slow-loris style connection draining."""

from __future__ import annotations

import logging
import threading
import time

from suijin.core.blue.errors import DeceptionError, ErrorSeverity, err, ok


class Tarpit:
    def __init__(self):
        self.active = {}
        self._lock = threading.Lock()

    def engage(self, ip: str, delay: float = 8.0):
        try:
            with self._lock:
                self.active[ip] = {"delay": delay, "count": 0, "started": time.time()}
            return ok(f"Tarpit engaged for {ip}")
        except Exception as e:
            logging.getLogger("suijin").warning(f"Tarpit engage failed: {e}")
            return err(DeceptionError(f"Tarpit failed: {e}", severity=ErrorSeverity.WARNING))

    def is_engaged(self, ip: str) -> bool:
        return ip in self.active

    def process(self, ip: str):
        if ip in self.active:
            self.active[ip]["count"] += 1
            time.sleep(self.active[ip]["delay"])

    def disengage(self, ip: str):
        try:
            with self._lock:
                if ip in self.active:
                    del self.active[ip]
        except Exception as e:
            logging.getLogger("suijin").warning(f"Tarpit disengage failed: {e}")
