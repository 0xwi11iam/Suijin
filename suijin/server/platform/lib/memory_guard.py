"""Memory guardrails — keep an overnight hunt from taking the box down.

The field failure (2026-11-17): a long hunt leaked Chromium/Python RSS
until the machine swapped hard. This module is the in-process half of the
fix; the host-level watchdog (`agent-vault/hunts/overnight/memory-watchdog.py`,
local-only) is the other half.

Design:
  - the guard samples its OWN process RSS every 15s (`ps -o rss=`) — cheap,
    no psutil dependency, works on macOS and Linux;
  - soft threshold (80%) logs a warning and runs a gc pass — the cheap
    fix for pure-Python growth;
  - hard threshold sends SIGTERM (the process's own cleanup chain runs,
    so no orphan browsers), then SIGKILL after a grace period;
  - role limits are overridable with SUIJIN_MEMORY_LIMIT_MB;
  - disabled by SUIJIN_MEMORY_GUARD=0 and always disabled under pytest
    (tests must not inherit a watchdog thread).

Never raises: a guard that crashes the thing it guards is worse than none.
"""

from __future__ import annotations

import gc
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_LIMITS = {
    "agent": 1800,        # one hunting agent: tools + browser bridges
    "coordinator": 1200,  # bus + sqlite + pool
    "web": 1400,          # console API (monitor-only — never killed)
}
GRACE_SECONDS = 10


def limit_for(role: str) -> int:
    """RSS ceiling for a role. SUIJIN_MEMORY_LIMIT_MB overrides every role."""
    override = os.environ.get("SUIJIN_MEMORY_LIMIT_MB", "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    return _DEFAULT_LIMITS.get(role, 1500)


def evaluate(rss_mb: float, limit_mb: float) -> str:
    """Pure decision function (unit-tested): ok / warn / kill."""
    if limit_mb <= 0:
        return "ok"
    if rss_mb >= limit_mb:
        return "kill"
    if rss_mb >= limit_mb * 0.8:
        return "warn"
    return "ok"


def _rss_mb(pid: int) -> float:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip()
        return int(out or 0) / 1024.0
    except Exception:  # noqa: BLE001 — a failed sample is not a failure
        return 0.0


def _log(line: str) -> None:
    try:
        path = Path.home() / ".suijin" / "logs" / "memory-guard.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with path.open("a") as fh:
            fh.write(f"{stamp} {line}\n")
    except Exception:  # noqa: BLE001
        pass


def start_guard(role: str, enforce: bool = True) -> bool:
    """Start the background sampler. Returns False when disabled.

    enforce=False (web console) logs only — killing the API server would
    take the operator's window down with it.
    """
    if os.environ.get("SUIJIN_MEMORY_GUARD", "1").strip() == "0":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    pid = os.getpid()
    limit = limit_for(role)

    def loop() -> None:
        warned = False
        while True:
            time.sleep(15)
            mb = _rss_mb(pid)
            if mb <= 0:
                continue
            verdict = evaluate(mb, limit)
            if verdict == "warn" and not warned:
                warned = True
                _log(f"{role} pid={pid} RSS={mb:.0f}MB warn (limit {limit}MB)")
                with _suppress():
                    gc.collect()
            elif verdict == "ok":
                warned = False
            elif verdict == "kill":
                _log(f"{role} pid={pid} RSS={mb:.0f}MB OVER limit {limit}MB — terminating")
                if not enforce:
                    continue
                with _suppress():
                    os.kill(pid, signal.SIGTERM)
                time.sleep(GRACE_SECONDS)
                with _suppress():
                    os.kill(pid, signal.SIGKILL)
                return

    threading.Thread(target=loop, daemon=True, name=f"memory-guard-{role}").start()
    _log(f"{role} pid={pid} guard armed (limit {limit}MB, enforce={enforce})")
    return True


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
