"""Background job management tools."""
from __future__ import annotations

import time


def _job_status(job_id: str) -> str:
    from medusa.nodes.execute_tool_node import _job_lock as node_lock
    from medusa.nodes.execute_tool_node import _jobs as node_jobs
    with node_lock:
        j = node_jobs.get(job_id)
    if not j:
        return f"Job {job_id} not found."
    elapsed = time.time() - j["started_at"]
    return (
        f"Job {job_id}: {j['status']} ({elapsed:.0f}s)\n"
        f"  Tool: {j['tool_name']}\n"
        f"  Args: {str(j.get('tool_args', {}))[:200]}\n"
        + (f"  Output: {str(j.get('output', ''))[:500]}" if j.get('output') else "  (no output yet)")
    )


def _job_wait(job_id: str, timeout: int = 60) -> str:
    from medusa.nodes.execute_tool_node import _job_lock as node_lock
    from medusa.nodes.execute_tool_node import _jobs as node_jobs
    deadline = time.time() + int(timeout)
    while time.time() < deadline:
        with node_lock:
            j = node_jobs.get(job_id)
        if not j:
            return f"Job {job_id} not found."
        if j["status"] in ("done", "failed", "cancelled"):
            return _job_status(job_id)
        time.sleep(1)
    return f"Job {job_id} still running after {timeout}s. Check job_status later."


def _job_output(job_id: str) -> str:
    from medusa.nodes.execute_tool_node import _job_lock as node_lock
    from medusa.nodes.execute_tool_node import _jobs as node_jobs
    with node_lock:
        j = node_jobs.get(job_id)
    if not j:
        return f"Job {job_id} not found."
    out = j.get("output", "")
    if not out:
        return f"Job {job_id}: {j['status']} — no output yet."
    return f"Job {job_id} output ({len(out)} chars):\n{out[:4000]}"


def _job_list() -> str:
    from medusa.nodes.execute_tool_node import _job_lock as node_lock
    from medusa.nodes.execute_tool_node import _jobs as node_jobs
    with node_lock:
        jobs = list(node_jobs.values())
    if not jobs:
        return "No background jobs."
    lines = []
    for j in jobs:
        elapsed = time.time() - j["started_at"]
        lines.append(f"  {j['job_id']}: {j['status']} ({elapsed:.0f}s) — {j['tool_name']}")
    return "Background jobs:\n" + "\n".join(lines)


def _job_cancel(job_id: str) -> str:
    from medusa.nodes.execute_tool_node import _job_lock as node_lock
    from medusa.nodes.execute_tool_node import _jobs as node_jobs
    with node_lock:
        j = node_jobs.get(job_id)
    if not j:
        return f"Job {job_id} not found."
    j["status"] = "cancelled"
    return f"Job {job_id} cancelled."
