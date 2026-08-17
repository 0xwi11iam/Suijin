"""Execute tool node — AI chooses sync or background (\"background\": true)."""
import logging
import threading
import time as _time
import uuid

from medusa.core.agent_context import set_phase_context, set_tenant_context
from medusa.core.prompt_safety import wrap_untrusted
from medusa.helpers.error_class import classify_error_class
from medusa.infra.output_offload import maybe_offload

logger = logging.getLogger(__name__)

_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()


def _spawn_background_job(tool_name: str, tool_args: dict, route_tool_fn) -> str:
    """Spawn a tool as a background thread. Returns job_id immediately."""
    job_id = uuid.uuid4().hex[:8]
    with _job_lock:
        _jobs[job_id] = {
            "job_id": job_id, "tool_name": tool_name, "tool_args": dict(tool_args),
            "status": "running", "started_at": _time.time(), "output": "", "error": None,
        }

    def _run():
        from medusa.tools.result import clear_stream_sink, set_stream_sink

        def sink(line: str):
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["output"] = (_jobs[job_id].get("output") or "") + line

        set_stream_sink(sink)
        try:
            result = route_tool_fn(tool_name, tool_args, {})
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["output"] = str(result)
                    _jobs[job_id]["status"] = "done"
        except Exception as e:
            with _job_lock:
                if job_id in _jobs:
                    _jobs[job_id]["error"] = str(e)
                    _jobs[job_id]["status"] = "failed"
        finally:
            clear_stream_sink()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    with _job_lock:
        _jobs[job_id]["_thread"] = t
    return job_id


async def execute_tool_node(state: dict, *, route_tool_fn) -> dict:
    """Execute tool. Set 'background': true in tool_args for async spawn."""
    step_data = state.get("_current_step", {})
    tool_name = step_data.get("tool_name")
    tool_args = dict(step_data.get("tool_args") or {})
    want_bg = tool_args.pop("background", False)

    logger.info(f"EXECUTE: {tool_name} bg={want_bg}")

    if not tool_name:
        return {"_current_step": {"tool_output": "No tool", "success": False}, "_tool_result": {"success": False}}

    # ── Meta-action: ask operator a question ─────────────────────────
    if tool_name == "ask_operator":
        question = tool_args.get("question", "Need guidance. Continue?")
        step_data.update({"tool_output": question, "success": True, "error_class": "ask_operator"})
        return {"_current_step": step_data, "_tool_result": {"success": True, "output": question},
                "_ask_operator": True,
                "messages": [{"role": "user", "content": f"AGENT QUESTION: {question}"}]}

    # ── Background spawn ──────────────────────────────────────────────
    if want_bg:
        job_id = _spawn_background_job(tool_name, tool_args, route_tool_fn)
        cmd = str(tool_args.get("cmd", tool_args.get("command", "")))[:150]
        output = f"BG JOB {job_id}: {tool_name} {cmd}\nCheck: job_status {job_id} | job_wait {job_id}"
        step_data.update({"tool_output": output, "success": True, "job_id": job_id,
                          "duration_ms": 0, "error_class": "background_spawn"})
        return {"_current_step": step_data, "_tool_result": {"success": True, "output": output},
                "messages": [{"role": "user", "content": f"BG JOB {job_id}: {tool_name}"}]}

    # ── Synchronous (10s timeout, auto-spawn if slower) ──────────────
    set_tenant_context("local", "default")
    set_phase_context(state.get("current_phase", "informational"))

    AUTO_BG_TIMEOUT = 10  # seconds before auto-promoting to background

    # Run in a thread so we can cap with join() timeout
    result_container = {}
    done_event = threading.Event()
    def _run_tool():
        try:
            result_container["result"] = route_tool_fn(tool_name, tool_args, {})
        except Exception as e:
            result_container["result"] = f"Tool error: {e}"
        finally:
            # If this thread was promoted to a bg job, update the job entry
            me = threading.current_thread()
            with _job_lock:
                for jid, job in list(_jobs.items()):
                    if job.get("_thread") is me:
                        res = str(result_container.get("result", ""))
                        job["output"] = res
                        job["status"] = "failed" if res.startswith("Error:") or res.startswith("Tool error:") else "done"
                        break
            done_event.set()

    t0 = _time.monotonic()
    t = threading.Thread(target=_run_tool, daemon=True)
    t.start()
    t.join(timeout=AUTO_BG_TIMEOUT)

    if t.is_alive():
        # Still running — promote to background job
        job_id = uuid.uuid4().hex[:8]
        with _job_lock:
            _jobs[job_id] = {
                "job_id": job_id, "tool_name": tool_name, "tool_args": dict(tool_args),
                "status": "running", "started_at": _time.time(), "output": "", "error": None,
                "_thread": t,
            }
        cmd = str(tool_args.get("cmd", tool_args.get("command", str(tool_args))))[:150]
        output = f"AUTO-BG {job_id}: {tool_name} (>{AUTO_BG_TIMEOUT}s)\n{cmd}\nCheck: job_status {job_id} | job_wait {job_id}"
        step_data.update({"tool_output": output, "success": True, "job_id": job_id,
                          "duration_ms": int((_time.monotonic() - t0) * 1000), "error_class": "auto_background"})
        return {"_current_step": step_data, "_tool_result": {"success": True, "output": output},
                "messages": [{"role": "user", "content": f"AUTO-BG {job_id}: {tool_name} was too slow, moved to background."}]}

    # Finished within timeout — return sync result
    result = result_container.get("result", "No output")

    output, _ = maybe_offload(tool_name, str(result))
    duration_ms = int((_time.monotonic() - t0) * 1000)
    success = not output.startswith("Error:") and not output.startswith("Tool error:")
    ec = classify_error_class(success=success, tool_output=output, error_message=output if not success else None,
                              duration_ms=duration_ms, tool_name=tool_name)

    step_data.update({"tool_output": output, "success": success, "duration_ms": duration_ms, "error_class": ec})
    return {"_current_step": step_data, "_tool_result": {"success": success, "output": output},
            "messages": [{"role": "user", "content": f"RESULT ({tool_name}):\n{wrap_untrusted(output, 'TOOL_OUTPUT')}"}]}
