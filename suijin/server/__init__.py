"""The engagement event bus — typed fan-out for one run.

The runner publishes every EngagementEvent here; subscribers consume:
the event-log writer (durable truth), the logging subprocess (the human
readable log/engagement.log), and any live view (TUI adapter, gateway).
In-process today; the gateway subscribes for remote clients.

Never raises into the publisher — a dead subscriber is dropped, not the
run.
"""

from __future__ import annotations

import contextlib
import threading


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list = []  # callables: (record: dict) -> None
        self._seq = 0

    # ── publish / subscribe ──────────────────────────────────────────
    def publish(self, kind: str, **fields) -> dict:
        """Fan one record to every subscriber. Returns the record."""
        self._seq += 1
        rec = {"kind": kind, "seq": self._seq}
        rec.update(fields)
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(rec)
            except Exception:  # noqa: BLE001 — a subscriber can never kill the run
                with contextlib.suppress(Exception):
                    self.unsubscribe(fn)
        return rec

    def subscribe(self, fn) -> None:
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)

    def unsubscribe(self, fn) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._subs.remove(fn)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)
