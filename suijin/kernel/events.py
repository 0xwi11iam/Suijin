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


# A subscriber that emits the SAME event re-entrantly would recurse until
# the stack dies. Depth-bounded: legitimate fan-out chains are shallow;
# anything past this is a loop, dropped with one warning (dmesg-style).
_MAX_EMIT_DEPTH = 10


class EventBus:
    """Synchronous pub/sub. Thread-safe. Failures are logged, not raised.

    Re-entrancy-bounded: emits nested deeper than _MAX_EMIT_DEPTH on one
    thread (always a subscriber loop) are dropped, not recursed."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = defaultdict(list)
        self._lock = threading.Lock()
        self._depth = threading.local()

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
        depth = getattr(self._depth, "n", 0)
        if depth >= _MAX_EMIT_DEPTH:
            logger.warning(
                "event emit dropped (event=%s): re-entrancy depth %d — a subscriber is emitting in a loop", event, depth
            )
            return
        with self._lock:
            subscribers = list(self._subs.get(event, ()))
        self._depth.n = depth + 1
        try:
            for fn in subscribers:
                try:
                    fn(payload)
                except Exception:  # noqa: BLE001 — isolation is the contract
                    logger.exception("event subscriber failed (event=%s)", event)
        finally:
            self._depth.n = depth

    def subscribers(self, event: str) -> int:
        """Introspection for tests + the boot report."""
        with self._lock:
            return len(self._subs.get(event, ()))
