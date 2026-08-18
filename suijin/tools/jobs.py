"""Background job management tools — thin wrappers over THE registry
(tools/job_registry.py). No more reaching into node privates."""

from __future__ import annotations

from suijin.tools.job_registry import (
    cancel,
    list_jobs,
    output,
    status,
    wait,
)


def _job_status(job_id: str) -> str:
    return status(job_id)


def _job_wait(job_id: str, timeout: int = 60) -> str:
    return wait(job_id, timeout=timeout)


def _job_output(job_id: str) -> str:
    return output(job_id)


def _job_list() -> str:
    jobs = list_jobs()
    if not jobs:
        return "No background jobs."
    lines = [f"{len(jobs)} job(s):"]
    for j in sorted(jobs, key=lambda x: x["started_at"], reverse=True):
        age = __import__("time").time() - j["started_at"]
        err = f"  ERROR: {j['error'][:80]}" if j.get("error") else ""
        lines.append(f"  {j['job_id']}  {j['status']:9} {age:>6.0f}s  {j['tool_name']}{err}")
    return "\n".join(lines)


def _job_cancel(job_id: str) -> str:
    ok = cancel(job_id)
    return f"Job {job_id} cancelled." if ok else f"Job {job_id} not found."
