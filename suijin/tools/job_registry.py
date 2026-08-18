"""THE job registry — single source of truth for background jobs.

Phase 0, item 4: two registries existed (runtime.py's was dead weight
re-exported by dispatch; the real one lived as privates inside
nodes/execute_tool_node.py, which tools/jobs.py reached into). The
registry now lives here; the node spawns through it and the job tools
read through it.

Pure stdlib; thread-safe; the thread target is injectable for tests.
"""

from __future__ import annotations

import threading
import time
import uuid

_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()

_MAX_TRACKED = 200  # bound memory on long engagements


def spawn(tool_name: str, tool_args: dict, fn, label: str = "") -> str:
    """Run fn(tool_name, tool_args, config) on a daemon thread; return job_id."""
    job_id = uuid.uuid4().hex[:8]
    entry = {
        "job_id": job_id,
        "tool_name": tool_name,
        "tool_args": dict(tool_args or {}),
        "status": "running",
        "started_at": time.time(),
        "output": "",
        "error": None,
        "label": label,
    }
    with _job_lock:
        _jobs[job_id] = entry
        # evict oldest finished jobs when over the cap
        if len(_jobs) > _MAX_TRACKED:
            finished = sorted(
                (k for k, v in _jobs.items() if v["status"] in ("done", "failed", "cancelled")),
                key=lambda k: _jobs[k]["started_at"],
            )
            for k in finished[: len(_jobs) - _MAX_TRACKED]:
                _jobs.pop(k, None)

    def _run():
        from suijin.tools.result import clear_stream_sink, set_stream_sink

        def sink(line: str):
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["output"] = (_jobs[job_id].get("output") or "") + line

        set_stream_sink(sink)
        try:
            result = fn(tool_name, tool_args, {})
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["output"] = str(result)
                    _jobs[job_id]["status"] = "done"
        except Exception as e:  # noqa: BLE001 — job failures are data
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["error"] = str(e)
                    _jobs[job_id]["status"] = "failed"
        finally:
            clear_stream_sink()

    t = threading.Thread(target=_run, daemon=True, name=f"job-{job_id}")
    t.start()
    with _job_lock:
        _jobs[job_id]["_thread"] = t
    return job_id


def get(job_id: str) -> dict | None:
    with _job_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None  # copy — callers can't mutate registry state


def status(job_id: str) -> str:
    j = get(job_id)
    if not j:
        return f"Job {job_id} not found."
    elapsed = time.time() - j["started_at"]
    return (
        f"Job {job_id}: {j['status']} ({elapsed:.0f}s)\n"
        f"  Tool: {j['tool_name']}\n"
        f"  Args: {str(j.get('tool_args', {}))[:200]}\n"
        + (f"  Output: {str(j.get('output', ''))[:500]}" if j.get("output") else "  (no output yet)")
    )


def wait(job_id: str, timeout: int = 60) -> str:
    deadline = time.time() + int(timeout)
    while time.time() < deadline:
        j = get(job_id)
        if not j:
            return f"Job {job_id} not found."
        if j["status"] in ("done", "failed", "cancelled"):
            return status(job_id)
        time.sleep(1)
    return f"Job {job_id} still running after {timeout}s. Check job_status later."


def output(job_id: str) -> str:
    j = get(job_id)
    if not j:
        return f"Job {job_id} not found."
    if j.get("error"):
        return f"Job {job_id} FAILED: {j['error']}\n{j.get('output', '')}"
    return str(j.get("output", ""))


def list_jobs() -> list[dict]:
    with _job_lock:
        return [dict(v) for v in _jobs.values()]


def cancel(job_id: str) -> bool:
    with _job_lock:
        j = _jobs.get(job_id)
        if not j:
            return False
        if j["status"] == "running":
            j["status"] = "cancelled"  # cooperative: the thread finishes but is ignored
            return True
        return True  # already terminal — idempotent success
