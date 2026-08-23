"""THE job registry — single source of truth for background jobs.

Phase 0, item 4: two registries existed (runtime.py's was dead weight
re-exported by dispatch; the real one lived as privates inside
nodes/execute_tool_node.py, which tools/jobs.py reached into). The
registry now lives here; the node spawns through it and the job tools
read through it.

Pure stdlib; thread-safe; the thread target is injectable for tests.
"""

from __future__ import annotations

import os
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
        from suijin.modules.tools.lib.result import clear_stream_sink, set_stream_sink

        def sink(line: str):
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["output"] = (_jobs[job_id].get("output") or "") + line

        set_stream_sink(sink)
        _active_job_id.value = job_id
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
            _active_job_id.value = None

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
    if j["status"] in ("done", "failed", "cancelled"):
        # finished: the full result IS the point — the old 500-char clip
        # hid the highest-value findings (field trace: the leaked-key scan
        # was never readable). Use job_output for more; here show plenty.
        body = str(j.get("output", "") or "(no output)")
        if j.get("error"):
            body = f"FAILED: {j['error']}\n{body}"
        return f"Job {job_id}: {j['status']} ({elapsed:.0f}s)\n  Tool: {j['tool_name']}\n{body}"
    return (
        f"Job {job_id}: {j['status']} ({elapsed:.0f}s)\n"
        f"  Tool: {j['tool_name']}\n"
        f"  Args: {str(j.get('tool_args', {}))[:200]}\n"
        + (f"  Output (partial): {str(j.get('output', ''))[:500]}" if j.get("output") else "  (no output yet)")
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


# job ids already drained into the conversation (H2) — drain each exactly once
_drained: set[str] = set()


def collect_finished_jobs(max_results: int = 3) -> list[str]:
    """H2: finished background jobs walk into the conversation (fireteam
    symmetry). Returns message strings for jobs that finished and were NOT
    yet announced; marks them drained. The agent can still pull full output
    via job_output — this is the tap on the shoulder, not the whole report."""
    msgs = []
    with _job_lock:
        finished = [
            (k, dict(v))
            for k, v in _jobs.items()
            if v.get("status") in ("done", "failed") and k not in _drained and v.get("_announce", True)
        ]
    for jid, j in sorted(finished, key=lambda kv: kv[1].get("started_at", 0))[:max_results]:
        _drained.add(jid)
        if len(_drained) > 200:  # bound memory on long engagements
            _drained.clear()
        out = str(j.get("output", "") or "")
        preview = out[:600] + (f" …(+{len(out) - 600} chars — job_output {jid})" if len(out) > 600 else "")
        state_word = "FINISHED" if j["status"] == "done" else f"FAILED ({j.get('error', '')})"
        msgs.append(f"BACKGROUND JOB {jid} {state_word} — {j.get('tool_name', '?')}\n{preview}")
    return msgs


def mark_announced(job_id: str) -> None:
    """Opt a job out of auto-announcement (e.g. the agent already read it)."""
    with _job_lock:
        _drained.add(job_id)


_active_job_id = threading.local()


def _register_proc(proc) -> None:
    """Called by result.py when a subprocess starts on this thread: attach
    it to the currently-running job so cancel() can killpg it."""
    jid = getattr(_active_job_id, "value", None)
    if jid is None:
        return
    with _job_lock:
        j = _jobs.get(jid)
        if j is not None:
            j["_proc"] = proc


def cancel(job_id: str) -> bool:
    """Cancel a job. v5.2: actually KILLS the subprocess process-group.

    The old version only flipped a status label (cooperative-only) —
    a cancelled nmap kept scanning in the background, invisible. Now:
    kill the process group of any tracked subprocess, then mark
    cancelled. Thread continues but its work dies with the process."""
    import signal

    with _job_lock:
        j = _jobs.get(job_id)
        if not j:
            return False
        if j["status"] == "running":
            # kill the subprocess if one is tracked for this job
            proc = j.get("_proc")
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    import contextlib

                    with contextlib.suppress(OSError):
                        proc.kill()  # fallback: direct kill
            j["status"] = "cancelled"
            return True
        return True  # already terminal — idempotent success
