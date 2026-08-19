"""Kernel journal — dmesg/journald analog.

In-memory ring buffer + rotated persistent log under the workspace.
Modules never touch it directly; the kernel and event bus write here,
doctor and the Module Manager read. Stdlib only. (Named 'journal' —
a kernel file named logging.py would shadow the stdlib module, exactly
the class of subtle bug this kernel exists to prevent.)

Flush semantics (v4 fix): entries are DRAINED atomically under the lock
— anything appended while the disk write is in flight lands in the ring
and is picked up by the next flush. The earlier snapshot-then-clear
implementation silently dropped those entries.
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
        self._ring: deque[tuple[float, str, str]] = deque()
        self._ring_size = ring_size
        self._dropped = 0  # entries displaced by flood (dmesg-style), COUNTED
        self._lock = threading.Lock()
        self._max_bytes = max_bytes
        self._path = self._dir / "journal.log"

    @property
    def dropped(self) -> int:
        """Entries displaced by ring overflow before a flush could drain
        them. Bounded memory is the design; SILENT loss is not."""
        with self._lock:
            return self._dropped

    def append(self, event: str, message: str) -> None:
        entry = (time.time(), event, str(message))
        with self._lock:
            if len(self._ring) >= self._ring_size:
                self._ring.popleft()
                self._dropped += 1
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
        """Persist pending entries to disk (rotating over the size cap).

        Atomic drain: the entries are removed from the ring under the
        lock BEFORE the (lock-free) disk write, so appends racing the
        write are preserved for the next flush — never lost.
        """
        with self._lock:
            entries = list(self._ring)
            self._ring.clear()
        if not entries:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            if self._path.exists() and self._path.stat().st_size > self._max_bytes:
                rotated = self._dir / f"journal-{int(time.time())}.log"
                self._path.replace(rotated)
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(self._path, "a") as f:
                for _ts, event, message in entries:
                    f.write(f"[{stamp}] {event}: {message}\n")
        except OSError:
            # disk write failed — put the entries BACK so the next flush
            # retries (a journal must never silently drop on disk errors)
            with self._lock:
                self._ring.extend(entries)
            raise
