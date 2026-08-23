"""Execute tool node — AI chooses sync or background (\"background\": true)."""

import logging
import threading
import time as _time

logger = logging.getLogger(__name__)

# THE job registry lives in tools/job_registry.py (Phase 0, item 4).
# These aliases keep older references working — they are the SAME objects.


def _jr():
    """Tools job registry (lazy: boundary rule)."""
    from suijin.modules.tools.lib import job_registry

    return job_registry


def _classify_error_class(*a, **k):
    from suijin.modules.platform.lib.helpers.error_class import classify_error_class

    return classify_error_class(*a, **k)


def _maybe_offload(*a, **k):
    from suijin.modules.platform.lib.infra.output_offload import maybe_offload

    return maybe_offload(*a, **k)


def __getattr__(name):
    if name in ("_job_lock", "_jobs"):
        return getattr(_jr(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _phase_ctx(*a, **k):
    from suijin.modules.platform.lib.agent_context import set_phase_context

    return set_phase_context(*a, **k)


def _tenant_ctx(*a, **k):
    from suijin.modules.platform.lib.agent_context import set_tenant_context

    return set_tenant_context(*a, **k)


def _wrap_untrusted(*a, **k):
    from suijin.modules.platform.lib.prompt_safety import wrap_untrusted

    return wrap_untrusted(*a, **k)


def _spawn_background_job(tool_name: str, tool_args: dict, route_tool_fn) -> str:
    """Spawn a tool as a background thread. Returns job_id immediately."""
    return _jr().spawn(tool_name, tool_args, route_tool_fn)


async def execute_tool_node(state: dict, *, route_tool_fn) -> dict:
    """Execute tool. Set 'background': true in tool_args for async spawn."""
    step_data = state.get("_current_step", {})
    tool_name = step_data.get("tool_name")
    tool_args = dict(step_data.get("tool_args") or {})
    want_bg = tool_args.pop("background", False)

    logger.info(f"EXECUTE: {tool_name} bg={want_bg}")

    if not tool_name:
        return {
            "_current_step": {"tool_output": "No tool", "success": False},
            "_tool_result": {"success": False},
        }

    # every outcome below returns the enriched step so the state merge can
    # REPLACE the trace entry think_node opened for this iteration — the
    # trace's success/error_class/tool_output finally reflect reality
    # (supervisor dead-end detection + truthful TUI failure markers)

    # ── Meta-action: ask operator a question ─────────────────────────
    if tool_name == "ask_operator":
        question = tool_args.get("question", "Need guidance. Continue?")
        step_data.update({"tool_output": question, "success": True, "error_class": "ask_operator"})
        return {
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": True, "output": question},
            "_ask_operator": True,
            "messages": [{"role": "user", "content": f"AGENT QUESTION: {question}"}],
        }

    # ── Background spawn ──────────────────────────────────────────────
    if want_bg:
        job_id = _spawn_background_job(tool_name, tool_args, route_tool_fn)
        cmd = str(tool_args.get("cmd", tool_args.get("command", "")))[:150]
        output = f"BG JOB {job_id}: {tool_name} {cmd}\nCheck: job_status {job_id} | job_wait {job_id}"
        step_data.update(
            {
                "tool_output": output,
                "success": True,
                "job_id": job_id,
                "duration_ms": 0,
                "error_class": "background_spawn",
            }
        )
        return {
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": True, "output": output},
            "messages": [{"role": "user", "content": f"BG JOB {job_id}: {tool_name}"}],
        }

    # ── Synchronous (auto-spawn if slower) ───────────────────────────
    _tenant_ctx("local", "default")
    _phase_ctx(state.get("current_phase", "informational"))

    # H2: job-control tools must NEVER be auto-backgrounded — waiting on a
    # slow job is the point of job_wait; promoting the wait itself to a
    # background job (the old behavior) made results uncollectable
    if tool_name in ("job_wait", "job_status", "job_output", "job_list", "fireteam_status"):
        t0_sync = _time.monotonic()
        result = route_tool_fn(tool_name, tool_args, {})
        output, _ = _maybe_offload(tool_name, str(result))
        duration_ms = int((_time.monotonic() - t0_sync) * 1000)
        success = not output.startswith("Error:")
        _audit_step(state, tool_name, tool_args, success, duration_ms)
        step_data.update(
            {"tool_output": output, "success": success, "duration_ms": duration_ms, "error_class": "success"}
        )
        return {
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": success, "output": output},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"RESULT ({tool_name}, {duration_ms}ms, iteration {step_data.get('iteration', '?')}):\n"
                        f"{_wrap_untrusted(output, 'TOOL_OUTPUT')}"
                    ),
                }
            ],
        }

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
            with _jr()._job_lock:
                for _jid, job in list(_jr()._jobs.items()):
                    if job.get("_thread") is me:
                        res = str(result_container.get("result", ""))
                        job["output"] = res
                        job["status"] = (
                            "failed" if res.startswith("Error:") or res.startswith("Tool error:") else "done"
                        )
                        break
            done_event.set()

    def _adopt() -> str:
        """Adopt the still-running thread into THE registry (no orphan)."""
        with _jr()._job_lock:
            job_id = __import__("uuid").uuid4().hex[:8]
            _jr()._jobs[job_id] = {
                "job_id": job_id,
                "tool_name": tool_name,
                "tool_args": dict(tool_args),
                "status": "running",
                "started_at": _time.time(),
                "output": "",
                "error": None,
                "_adopted": True,
                "_thread": t,
                # completion signal: the waiter above resolves status when
                # the adopted thread finishes
                "_done_event": done_event,
            }
        return job_id

    t0 = _time.monotonic()
    t = threading.Thread(target=_run_tool, daemon=True)
    t.start()
    t.join(timeout=AUTO_BG_TIMEOUT)

    if t.is_alive():
        # Still running — promote to background job via the registry
        job_id = _adopt()
        cmd = str(tool_args.get("cmd", tool_args.get("command", str(tool_args))))[:150]
        output = f"AUTO-BG {job_id}: {tool_name} (>{AUTO_BG_TIMEOUT}s)\n{cmd}\nCheck: job_status {job_id} | job_wait {job_id}"
        step_data.update(
            {
                "tool_output": output,
                "success": True,
                "job_id": job_id,
                "duration_ms": int((_time.monotonic() - t0) * 1000),
                "error_class": "auto_background",
            }
        )
        return {
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": True, "output": output},
            "messages": [
                {"role": "user", "content": f"AUTO-BG {job_id}: {tool_name} was too slow, moved to background."}
            ],
        }

    # Finished within timeout — return sync result
    result = result_container.get("result", "No output")

    output, _ = _maybe_offload(tool_name, str(result))
    duration_ms = int((_time.monotonic() - t0) * 1000)
    # failure prefixes match dispatch's repeat-guard set — "HTTP Error:" and
    # "Execution Fault:" were missing here, so HTTP failures registered as
    # SUCCESS in the trace (the '!' marker never fired for them either)
    _FAILS = ("Error:", "Tool error:", "Tool Error", "HTTP Error:", "Execution Fault:")
    success = not str(output).startswith(_FAILS)
    _audit_step(state, tool_name, tool_args, success, duration_ms)
    ec = _classify_error_class(
        success=success,
        tool_output=output,
        error_message=output if not success else None,
        duration_ms=duration_ms,
        tool_name=tool_name,
    )

    step_data.update({"tool_output": output, "success": success, "duration_ms": duration_ms, "error_class": ec})

    # H1: engagement state board — harvest what this tool learned and merge
    # it into target_info; grew flag replaces the fake same-dict growth
    # comparison think_node used to run
    board_updates: dict = {}
    grew = False
    if success:
        try:
            from suijin.modules.agent.lib.target_board import extract_from_output, merge_updates

            upd = extract_from_output(tool_name, tool_args, output)
            if upd:
                merged, grew = merge_updates(state.get("target_info") or {}, upd)
                board_updates = {"target_info": merged}
        except Exception:  # noqa: BLE001 — the board must never break a step
            pass

    # H3: populate chain_failures_memory — the 'Recent Failures' context
    # section existed but was never written (dead prompt block)
    failure_updates: dict = {}
    if not success and ec not in ("background_spawn", "auto_background", "ask_operator"):
        try:
            fails = list(state.get("chain_failures_memory") or [])
            fails.append(
                {
                    "tool_name": tool_name,
                    "error_class": ec,
                    "error_message": output[:160],
                    "iteration": step_data.get("iteration"),
                }
            )
            failure_updates = {"chain_failures_memory": fails[-15:]}
        except Exception:  # noqa: BLE001
            pass

    return {
        "_current_step": step_data,
        "execution_trace": [dict(step_data)],
        "_tool_result": {"success": success, "output": output},
        **board_updates,
        **failure_updates,
        "_target_grew_last_step": grew,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"RESULT ({tool_name}, {duration_ms}ms, iteration {step_data.get('iteration', '?')}):\n"
                    f"{_wrap_untrusted(output, 'TOOL_OUTPUT')}"
                ),
            }
        ],
    }


def _audit_step(state, tool_name, tool_args, success, duration_ms):
    """Append the step to the engagement audit trail (never raises).

    H6: iteration comes from the EXECUTING STEP first — current_iteration
    isn't set in state at execute time, so every row in every
    agent_steps.jsonl read 'iteration=?' (per-iteration forensics were
    impossible)."""
    try:
        from suijin.kernel.audit import ToolAudit
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        iteration = (state.get("_current_step") or {}).get("iteration") or state.get("current_iteration") or "?"
        ToolAudit(WORKSPACE_DIR / "outputs" / "audit_trails", "agent_steps.jsonl", flush_every=1).record(
            surface="agent",
            name=tool_name,
            args=tool_args,
            outcome="ok" if success else "tool-error",
            duration_ms=duration_ms,
            detail=f"iteration={iteration}",
        )
    except Exception:  # noqa: BLE001 — audit must never break execution
        pass
