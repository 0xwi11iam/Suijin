"""Tarpit — slow-loris style connection draining."""
from __future__ import annotations
import time, threading, logging
from medusa.core.blue.errors import DeceptionError, ErrorSeverity, ok, err

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
            logging.getLogger("medusa").warning(f"Tarpit engage failed: {e}")
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
            logging.getLogger("medusa").warning(f"Tarpit disengage failed: {e}")
