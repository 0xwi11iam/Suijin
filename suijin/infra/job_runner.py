"""Background job runner for long-running tool calls.

Manages asyncio Tasks for tools that take >30s (sqlmap, hydra, nmap deep scans).
Each job's stdout is tee'd to suijin_agent/outputs/<job_id>.log.

Ported and simplified from redamon/agentic/job_runner.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from suijin.tools.workspace import WORKSPACE_DIR

# Sandbox config — defined inline
SANDBOX_CONFIG = {
    "blocked_commands": [
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "init",
        "systemctl",
        "service",
        "crontab",
        "at",
        "batch",
        "mount",
        "umount",
        "mkfs",
        "fdisk",
        "parted",
        "iptables",
        "nftables",
        "ufw",
        "firewall-cmd",
    ],
    "writable_paths": ["/tmp/suijin_sandbox", "/var/tmp/suijin"],
}
SANDBOX_RESOURCE_LIMITS = {
    "max_runtime_seconds": 300,
    "max_memory_mb": 512,
    "max_cpu_percent": 50,
    "max_disk_write_mb": 100,
    "max_processes": 10,
    "max_open_files": 64,
}


def is_command_allowed(command: str) -> bool:
    cmd_parts = command.strip().split()
    if not cmd_parts:
        return False
    return cmd_parts[0].split("/")[-1] not in SANDBOX_CONFIG["blocked_commands"]


def get_sandbox_workdir() -> str:
    workdir = WORKSPACE_DIR / "sandbox"
    workdir.mkdir(parents=True, exist_ok=True)
    return str(workdir)


REPORT_CONFIG = {"default_format": "markdown", "include_mermaid_diagrams": True}
WEB_API_CONFIG = {"cors_origins": ["http://localhost:3000"], "rate_limit_per_minute": 60}

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = WORKSPACE_DIR


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobHandle:
    job_id: str
    tool_name: str
    args: dict
    label: Optional[str]
    status: str  # running | done | failed | cancelled
    started_at: str
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    output_path: str = ""
    error: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "task"}


class JobRegistry:
    """Holds running background jobs, keyed by job_id."""

    def __init__(self):
        self._jobs: dict[str, JobHandle] = {}
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        tool_name: str,
        args: dict,
        runner: Callable[[str, dict, Callable[[str], None]], Awaitable[tuple[int, str]]],
        label: Optional[str] = None,
    ) -> JobHandle:
        """Spawn a background job. Returns immediately with a handle."""
        job_id = uuid.uuid4().hex[:12]
        outputs_dir = WORKSPACE_ROOT / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        log_path = outputs_dir / f"job_{job_id}.log"
        meta_path = outputs_dir / f"job_{job_id}.meta.json"

        handle = JobHandle(
            job_id=job_id,
            tool_name=tool_name,
            args=args,
            label=label,
            status="running",
            started_at=_utc_now_str(),
            output_path=str(log_path),
        )

        async with self._lock:
            self._jobs[job_id] = handle

        async def _run():
            log_lines = []

            def append_log(chunk: str):
                log_lines.append(chunk)

            try:
                exit_code, error = await runner(tool_name, args, append_log)
                handle.exit_code = exit_code
                handle.error = error
                handle.status = "failed" if exit_code != 0 or error else "done"
            except asyncio.CancelledError:
                handle.status = "cancelled"
                handle.error = "Cancelled by user"
            except Exception as e:
                handle.status = "failed"
                handle.error = str(e)
            finally:
                handle.ended_at = _utc_now_str()
                # Write log
                log_path.write_text("".join(log_lines), encoding="utf-8", errors="ignore")
                # Write meta
                meta_path.write_text(json.dumps(handle.to_dict(), indent=2))

        handle.task = asyncio.create_task(_run())
        return handle

    async def get(self, job_id: str) -> Optional[JobHandle]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> bool:
        async with self._lock:
            h = self._jobs.get(job_id)
            if h and h.task and not h.task.done():
                h.task.cancel()
                return True
        return False

    async def list_jobs(self) -> list[dict]:
        async with self._lock:
            return [h.to_dict() for h in self._jobs.values()]


# Singleton
_registry: Optional[JobRegistry] = None


def get_job_registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry()
    return _registry
