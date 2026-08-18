"""Kernel journal — dmesg/journald analog.

In-memory ring buffer + rotated persistent log under the workspace.
Modules never touch it directly; the kernel and event bus write here,
doctor and the Module Manager read. Stdlib only. (Named 'journal' —
a kernel file named logging.py would shadow the stdlib module, exactly
the class of subtle bug this kernel exists to prevent.)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path


class Journal:
    """Ring buffer of (ts, event, message) + optional disk persistence."""

    def __init__(self, log_dir: Path, ring_size: int = 1000, max_bytes: int = 1_000_000) -> None:
        self._dir = Path(log_dir)
        self._ring: deque[tuple[float, str, str]] = deque(maxlen=ring_size)
        self._lock = threading.Lock()
        self._max_bytes = max_bytes
        self._path = self._dir / "journal.log"

    def append(self, event: str, message: str) -> None:
        entry = (time.time(), event, str(message))
        with self._lock:
            self._ring.append(entry)

    def tail(self, n: int = 50) -> list[str]:
        """Most recent entries, oldest first, formatted."""
        with self._lock:
            entries = list(self._ring)
        out = []
        for ts, event, message in entries[-n:]:
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            out.append(f"[{stamp}] {event}: {message}")
        return out

    def flush(self) -> None:
        """Persist the ring to disk (rotating when over the size cap)."""
        with self._lock:
            entries = list(self._ring)
        if not entries:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and self._path.stat().st_size > self._max_bytes:
            rotated = self._dir / f"journal-{int(time.time())}.log"
            self._path.replace(rotated)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self._path, "a") as f:
            for ts, event, message in entries:
                f.write(f"[{stamp}] {event}: {message}\n")
        with self._lock:
            self._ring.clear()  # persisted — ring starts fresh
