"""The resource governor — shared process pool, disk batching (2026-11-05).

At 100 agents: each spawning subprocesses freely = fork bomb. Every
tool writing JSONL = random I/O. No coordination = thundering herd.

The governor runs on the coordinator and provides:
  - Process pool: N concurrent subprocesses max, priority queue
  - Disk batching: accumulate writes, flush every 100ms
  - Connection pool: shared HTTP sessions
"""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path


class ProcessPool:
    """Bounded subprocess execution with priority queueing.

    Priority: exploitation (2) > recon (1) > background (0).
    Default max_workers = os.cpu_count() (scales with the machine).
    Timeout enforced at the pool level (not per-caller).
    """

    def __init__(self, max_workers: int = 0, default_timeout: int = 30):
        self._max = max_workers or os.cpu_count() or 4
        self._timeout = default_timeout
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._workers: list[threading.Thread] = []
        self._running = False
        self._active = 0
        self._lock = threading.Lock()
        self._zombie_reaper: threading.Thread | None = None

    def start(self):
        self._running = True
        for i in range(self._max):
            t = threading.Thread(target=self._worker, daemon=True, name=f"pool-{i}")
            t.start()
            self._workers.append(t)
        self._zombie_reaper = threading.Thread(target=self._reap_zombies, daemon=True, name="reaper")
        self._zombie_reaper.start()

    def stop(self):
        self._running = False

    def execute(self, cmd: list[str], priority: int = 1, timeout: int | None = None) -> subprocess.CompletedProcess:
        """Submit a command. Blocks until complete (or timeout).
        Returns the CompletedProcess. Raises TimeoutExpired."""
        result_q: queue.Queue = queue.Queue()
        self._queue.put((-priority, time.time(), (cmd, timeout or self._timeout, result_q)))
        try:
            return result_q.get(timeout=(timeout or self._timeout) + 10)
        except queue.Empty:
            return subprocess.CompletedProcess(cmd, -1, "", "pool timeout — queue full or worker stuck")

    def _worker(self):
        while self._running:
            try:
                _, _, (cmd, timeout, result_q) = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            with self._lock:
                self._active += 1
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
                result_q.put(proc)
            except subprocess.TimeoutExpired:
                result_q.put(subprocess.CompletedProcess(cmd, -1, "", f"timeout after {timeout}s"))
            except Exception as e:  # noqa: BLE001
                result_q.put(subprocess.CompletedProcess(cmd, -1, "", str(e)))
            finally:
                with self._lock:
                    self._active -= 1

    def _reap_zombies(self):
        """Kill orphaned processes every 60s (children of dead workers)."""
        while self._running:
            time.sleep(60)
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["pkill", "-f", "suijin.*defunct"],
                    capture_output=True,
                    timeout=5,
                )

    @property
    def stats(self) -> dict:
        return {
            "max_workers": self._max,
            "active": self._active,
            "queued": self._queue.qsize(),
        }


class DiskBatcher:
    """Batches write operations — accumulate in memory, flush on interval.

    At 100 agents × 5 writes/iteration × 1 iteration/10s = 50 writes/sec
    of random I/O. The batcher collects them and flushes one transaction
    every 100ms — 10× fewer I/O operations, sequential not random.
    """

    def __init__(self, flush_interval_s: float = 0.1, max_batch: int = 100):
        self._interval = flush_interval_s
        self._max_batch = max_batch
        self._pending: list[tuple[str, str]] = []  # (path, content)
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stats = {"flushed": 0, "batches": 0, "dropped": 0}

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="disk-batcher")
        self._thread.start()

    def stop(self):
        self._running = False
        self._flush()  # final flush

    def write(self, path: str, content: str):
        """Queue a write. Content is appended (for log-style writes)."""
        with self._lock:
            if len(self._pending) < self._max_batch * 10:  # hard cap
                self._pending.append((path, content))
            else:
                self._stats["dropped"] += 1

    def _flush_loop(self):
        while self._running:
            time.sleep(self._interval)
            self._flush()

    def _flush(self):
        with self._lock:
            batch = self._pending
            self._pending = []
        if not batch:
            return

        # group by path, concatenate appends
        by_path: dict[str, list[str]] = defaultdict(list)
        for path, content in batch:
            by_path[path].append(content)

        for path, contents in by_path.items():
            with contextlib.suppress(OSError):
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write("\n".join(contents) + "\n")

        self._stats["flushed"] += len(batch)
        self._stats["batches"] += 1

    @property
    def stats(self) -> dict:
        return {**self._stats, "pending": len(self._pending)}


class ConnectionPool:
    """Shared HTTP session pool — one pool, not 100 per-agent pools.

    Uses requests.Session with keep-alive. Sessions are reused across
    all agents, reducing TCP handshakes by ~90%.
    """

    def __init__(self, pool_size: int = 10):
        self._pool_size = pool_size
        self._sessions: list = []
        self._lock = threading.Lock()

    def get(self):
        """Get a session from the pool (or create one)."""
        with self._lock:
            if self._sessions:
                return self._sessions.pop()
        import requests

        s = requests.Session()
        s.adapters["https://"].pool_maxsize = self._pool_size
        return s

    def put(self, session):
        with self._lock:
            if len(self._sessions) < self._pool_size:
                self._sessions.append(session)

    def close(self):
        with self._lock:
            for s in self._sessions:
                with contextlib.suppress(Exception):
                    s.close()
            self._sessions.clear()
