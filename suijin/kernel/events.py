"""Kernel event bus — synchronous pub/sub with per-subscriber isolation.

Replaces every lazy cross-import hook in the tree (supervisor checks,
oracle probes, audit logging): publishers emit, subscribers subscribe,
and a broken subscriber can never break the chain. Stdlib only.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("suijin.kernel.events")

Subscriber = Callable[[Any], None]


class EventBus:
    """Synchronous pub/sub. Thread-safe. Failures are logged, not raised."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event: str, fn: Subscriber) -> None:
        """Subscribe fn to event."""
        with self._lock:
            if fn not in self._subs[event]:
                self._subs[event].append(fn)

    def off(self, event: str, fn: Subscriber) -> None:
        """Unsubscribe fn from event (no-op when absent)."""
        with self._lock, contextlib.suppress(ValueError):
            self._subs[event].remove(fn)

    def emit(self, event: str, payload: Any = None) -> None:
        """Deliver to every subscriber; each failure is isolated + logged."""
        with self._lock:
            subscribers = list(self._subs.get(event, ()))
        for fn in subscribers:
            try:
                fn(payload)
            except Exception:  # noqa: BLE001 — isolation is the contract
                logger.exception("event subscriber failed (event=%s)", event)

    def subscribers(self, event: str) -> int:
        """Introspection for tests + the boot report."""
        with self._lock:
            return len(self._subs.get(event, ()))
