"""Kernel job scheduler — spawn/status/wait/cancel with caps.

The kernel twin of tools/job_registry.py (Phase 2 merges them: the tools
registry delegates here). Pure stdlib; the work fn is injectable.
"""

from __future__ import annotations

import threading
import time
import uuid

_MAX_TRACKED = 200


class JobScheduler:
    """Thread-safe background job registry owned by the Context."""

    def __init__(self, max_tracked: int = _MAX_TRACKED) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._max = max_tracked

    def spawn(self, tool_name: str, tool_args: dict, fn) -> str:
        """Run fn(tool_name, tool_args, config) on a daemon thread."""
        jid = uuid.uuid4().hex[:8]
        with self._lock:
            self._jobs[jid] = {
                "id": jid,
                "tool_name": tool_name,
                "tool_args": dict(tool_args or {}),
                "status": "running",
                "started_at": time.time(),
                "output": "",
                "error": None,
            }
            if len(self._jobs) > self._max:
                finished = sorted(
                    (k for k, v in self._jobs.items() if v["status"] in ("done", "failed", "cancelled")),
                    key=lambda k: self._jobs[k]["started_at"],
                )
                for k in finished[: len(self._jobs) - self._max]:
                    self._jobs.pop(k, None)

        def _run():
            try:
                result = fn(tool_name, tool_args, None)
                with self._lock:
                    if jid in self._jobs and self._jobs[jid]["status"] != "cancelled":
                        self._jobs[jid]["output"] = str(result)
                        self._jobs[jid]["status"] = "done"
            except Exception as e:  # noqa: BLE001 — failures are data
                with self._lock:
                    if jid in self._jobs:
                        self._jobs[jid]["error"] = str(e)
                        self._jobs[jid]["status"] = "failed"

        threading.Thread(target=_run, daemon=True, name=f"kjob-{jid}").start()
        return jid

    def get(self, jid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(jid)
            return dict(j) if j else None

    def status(self, jid: str) -> str:
        j = self.get(jid)
        return j["status"] if j else "not found"

    def output(self, jid: str) -> str:
        j = self.get(jid)
        if not j:
            return f"job {jid} not found"
        if j["error"]:
            return f"FAILED: {j['error']}\n{j['output']}"
        return j["output"]

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._jobs.values()]

    def cancel(self, jid: str) -> bool:
        with self._lock:
            j = self._jobs.get(jid)
            if not j:
                return False
            if j["status"] == "running":
                j["status"] = "cancelled"
            return True
