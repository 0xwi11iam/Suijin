"""Execute tool node — AI chooses sync or background (\"background\": true)."""

import asyncio as _asyncio
import contextlib
import logging
import threading
import time as _time

logger = logging.getLogger(__name__)

# THE job registry lives in tools/job_registry.py (Phase 0, item 4).
# These aliases keep older references working — they are the SAME objects.


def _artifact_dir(name):
    from suijin.modules.platform.lib.workspace import artifact_dir

    return artifact_dir(name)


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


def _harvest_findings(state: dict, tool_name: str, output: str) -> dict:
    """H4: catalog CONFIRMED -> state[findings] + the engagement schema.
    The scoreboard and the SQLi/SSRF chain rules read findings every turn;
    the schema's findings/stats list was never populated by anything.
    Used by BOTH return paths (the never-bg verifier branch AND the sync
    thread branch) — the inline version missed catalog_exploit entirely
    once the POC takeover made it never-auto-backgrounded."""
    if tool_name != "catalog_exploit":
        return {}
    with contextlib.suppress(Exception):
        import re as _re

        m = _re.search(r"(EXP-\d+)\s+CONFIRMED\s+—\s+(.+?)(?:\.\s|\n|$)", str(output))
        if not m:
            return {}
        founds = list(state.get("findings") or [])
        founds.append({"id": m.group(1), "type": "exploit", "title": m.group(2)[:120]})
        from suijin.modules.agent.lib.engagement import add_finding_to_schema

        add_finding_to_schema({"id": m.group(1), "title": m.group(2)[:120], "source": "catalog_exploit"})
        return {"findings": founds[-20:]}
    return {}


#: Recon/surface-mapping tools (2026-10-20 go-bigger): while saturated
#: with trivia findings, calls to these get the saturation warning
#: prepended — soft-tagged, never refused.
_RECON_TOOLS = frozenset(
    {
        "surface_expand",
        "crawl",
        "api_discover",
        "js_bundle_analyze",
        "source_map_probe",
        "dom_recon",
        "smart_fetch",
    }
)


def _satiation_tag(state: dict, tool_name: str, output: str) -> str:
    """Prepend the saturation warning to recon-tool output while the
    agent is saturated with trivia. Soft nudge — the call still runs,
    the result still lands, but the model reads the cost of staying
    in scanner mode right on top of it."""
    if tool_name not in _RECON_TOOLS:
        return output
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib.weaponization import satiation

        if satiation(state).get("saturated"):
            return (
                "[SURFACE SATURATED — 4+ informational findings, zero high-impact ones. "
                "More surface mapping adds NOTHING. Test a high-weight class instead: "
                "authn / authz / idor / sqli — auth_forge, cred_matrix, race_test.]\n\n" + str(output)
            )
    return output


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
        # AUTH REDIRECT (2026-10-26): the agent gravitates to ask_operator
        # for credential requests even when request_auth exists. When the
        # question is about login/credentials/authentication, redirect to
        # the auth picker instead — the operator gets numbered choices,
        # not a free-text prompt they have to figure out.
        _q = question.lower()
        if any(
            kw in _q
            for kw in (
                "credential",
                "username",
                "password",
                "login",
                "authenticated access",
                "authentication",
                "sign in",
                "auth wall",
                "session cookie",
                "bearer token",
                "api key",
            )
        ):
            # extract target from the question (URL/host pattern)
            import re as _re

            _url_m = _re.search(r"https?://[^\s,)+\"]+|[\w.-]+\.\w{2,}:\d+", question)
            _target = _url_m.group(0) if _url_m else "target"
            question = (
                f"AUTH NEEDED: {_target} — authentication required\n\n"
                f"[1] Provide credentials (type: 1 user:password)\n"
                f"[2] Paste a token/cookie/API key (type: 2 <value>)\n"
                f"[3] Browser login (a Chromium window opens, you log in)\n"
                f"[4] Skip (test unauthenticated only)\n\n"
                f"Type a number (1-4) in the input box. Anything else = skip."
            )
        step_data.update({"tool_output": question, "success": True, "error_class": "ask_operator"})
        return {
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": True, "output": question},
            "messages": [{"role": "user", "content": f"AGENT QUESTION: {question}"}],
        }

    # ── chain_track: advance THE LOOM (2026-10-29) ────────────────────
    # The harness holds the chain so the model doesn't have to — confirm
    # or kill nodes as primitives land; the loom re-renders every turn.
    if tool_name == "chain_track":
        from suijin.server.tools.lib.chain_engine import loom_render, template_ids, track

        _tid = str(tool_args.get("template", "")).strip()
        _action = str(tool_args.get("action", "")).strip().lower()
        _node = str(tool_args.get("node", "")).strip()
        _note = str(tool_args.get("note", "")).strip()
        if _tid not in template_ids():
            out = f"Unknown chain template '{_tid}'. Known: {', '.join(template_ids())}"
        elif _action not in ("start", "confirm", "dead"):
            out = "Error: action must be start | confirm | dead (node= and note= optional)"
        else:
            new_plan = track((state or {}).get("chain_plan"), _tid, _action, node=_node, note=_note)
            out = f"chain loom updated ({_tid}: {_action}" + (f", node={_node}" if _node else "") + ")\n"
            out += loom_render(new_plan)
            if _action == "confirm":
                # remind what this confirmation unlocks
                with contextlib.suppress(Exception):
                    unlocked = (new_plan.get(_tid) or {}).get("unlocked") or []
                    if unlocked:
                        out += "\nunlocked by this confirmation: " + ", ".join(unlocked)
            return {
                "chain_plan": new_plan,
                "_current_step": step_data,
                "execution_trace": [dict(step_data)],
                "_tool_result": {"success": True, "output": out},
                "messages": [{"role": "user", "content": out}],
            }

    # ── request_auth: the AUTH PICKER (2026-10-26) ─────────────────
    # Routes through the ask mechanism with numbered choices so the
    # operator gets an interactive picker, not a text prompt the agent
    # relays. The answer determines the credential handoff mode.
    if tool_name == "request_auth":
        target = tool_args.get("target", "")
        hint = tool_args.get("hint", "authentication required")
        login_url = tool_args.get("login_url", f"https://{target}/login")
        cred_name = tool_args.get("credential_name", target.replace(":", "_") if target else "auth")
        question = (
            f"AUTH NEEDED: {target} — {hint}\n"
            f"Login URL: {login_url}\n\n"
            f"[1] Provide credentials (type: user:password)\n"
            f"[2] Paste a token/cookie/API key\n"
            f"[3] Browser login (a Chromium window opens, you log in)\n"
            f"[4] Skip (test unauthenticated only)\n\n"
            f"Type a number (1-4), or for [1] type: 1 user:pass\n"
            f"For [2] type: 2 <token-value>"
        )
        step_data.update({"tool_output": question, "success": True, "error_class": "ask_operator"})
        env_var = f"SUIJIN_CRED_{cred_name.upper()}"
        with contextlib.suppress(Exception):
            from suijin.modules.credential_store.main import env_name_for

            env_var = env_name_for(cred_name)
        return {
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": True, "output": question},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"AGENT QUESTION: {question}\n\n"
                        f"The operator's answer determines the credential. Parse it:\n"
                        f"- starts with '1' → credentials follow (user:pass)\n"
                        f"- starts with '2' → a token/cookie value follows\n"
                        f"- is '3' → browser login mode: call request_auth again with "
                        f"login_url='{login_url}' to trigger the browser capture\n"
                        f"- is '4' or 'skip' → no auth, continue unauthenticated\n\n"
                        f"After parsing, store the credential:\n"
                        f"creds_add(service='{cred_name}', type='cookie', value='<the value>')\n"
                        f"Then use ${env_var} in execute_terminal commands (auto-exported — never "
                        f"paste the literal into commands or files), or "
                        f"http_request(url='http://{target}/api', credential='{cred_name}')"
                    ),
                }
            ],
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
    # background job (the old behavior) made results uncollectable.
    # catalog_exploit: the POC verifier TAKES OVER the loop by contract —
    # the AI pauses until the yaml finishes (bounded by its 120s wall
    # cap); backgrounding it would break the takeover and let the AI
    # keep thinking mid-verification.
    # The wait runs OFF the event loop (to_thread): model-authored JSON
    # once asked for job_wait timeout=3600 and froze the whole pipeline
    # — interrupts, the reader, everything — for an hour; the timeout is
    # CLAMPED so the model can never choose its own freeze duration.
    if tool_name in ("job_wait", "job_status", "job_output", "job_list", "fireteam_status", "catalog_exploit"):
        if tool_name == "job_wait":
            with contextlib.suppress(Exception):
                tool_args["timeout"] = min(max(float(tool_args.get("timeout", 30) or 30), 1.0), 120.0)
        _t0_sync = _time.monotonic()
        result = await _asyncio.to_thread(route_tool_fn, tool_name, tool_args, {})
        output, _ = _maybe_offload(tool_name, str(result))
        output = _satiation_tag(state, tool_name, str(output))
        with contextlib.suppress(Exception):
            from suijin.modules.agent.lib.loop_detection import record_tool_output

            record_tool_output(tool_name, tool_args, output)
        duration_ms = int((_time.monotonic() - _t0_sync) * 1000)
        success = not output.startswith("Error:")
        _audit_step(state, tool_name, tool_args, success, duration_ms)
        step_data.update(
            {"tool_output": output, "success": success, "duration_ms": duration_ms, "error_class": "success"}
        )
        # CHAIN INDICATOR FIRING (2026-10-29): scan every tool result for
        # chain indicators — the association surfaces AT OBSERVATION TIME
        # (the mechanized "organic" chain thinking). Fired-once per
        # engagement, deduped in state.
        _extra_state: dict = {}
        _msg = _state_result_message(tool_name, duration_ms, step_data, output, state)
        if tool_name not in ("chain_suggest", "chain_track"):
            with contextlib.suppress(Exception):
                from suijin.server.tools.lib.chain_engine import scan_indicators

                _fired_before = list((state or {}).get("_chain_fired") or [])
                _lines, _new = scan_indicators(str(output), _fired_before)
                if _lines:
                    _msg += "\n" + _lines
                    _extra_state["_chain_fired"] = _fired_before + _new

        # TUNNEL-VISION SIGNAL (2026-10-29, harness-level): one good tool
        # used all day while coverage gaps sit untouched — the operator's
        # core-AI-bug complaint. Third+ consecutive call to the SAME tool
        # with no fresh finding in this result → the result carries the
        # gaps + an alternative. Progress exempts (CONFIRMED/credential).
        with contextlib.suppress(Exception):
            _tv = _tunnel_vision_notice(state, tool_name, tool_args, str(output))
            if _tv:
                _msg = _tv + "\n" + _msg
        return {
            **_harvest_findings(state, tool_name, output),
            **_extra_state,
            "_current_step": step_data,
            "execution_trace": [dict(step_data)],
            "_tool_result": {"success": success, "output": output},
            "messages": [
                {
                    "role": "user",
                    "content": _msg,
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
    from suijin.kernel.diag import diag_timer

    with diag_timer("tool", name=tool_name, bg=True):
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
    output = _satiation_tag(state, tool_name, str(output))
    # REPEAT-ECHO (2026-10-20): record for the loop detector's nudge
    with contextlib.suppress(Exception):
        from suijin.modules.agent.lib.loop_detection import record_tool_output

        record_tool_output(tool_name, tool_args, output)
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
        # the librarian's nerve ending: every successful result lands on
        # its desk (fire-and-forget; no-op headless)
        try:
            from suijin.modules.agent.lib import librarian as _lb

            _lb.observe(tool_name, tool_args, output, step_data.get("iteration") or 0)
        except Exception:  # noqa: BLE001
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

    finding_updates = _harvest_findings(state, tool_name, output)

    return {
        "_current_step": step_data,
        "execution_trace": [dict(step_data)],
        "_tool_result": {"success": success, "output": output},
        **board_updates,
        **failure_updates,
        **finding_updates,
        "_target_grew_last_step": grew,
        "messages": [
            {
                "role": "user",
                "content": _state_result_message(tool_name, duration_ms, step_data, output, state),
            }
        ],
    }


def _tunnel_vision_notice(state: dict, tool_name: str, tool_args: dict, output: str) -> str:
    """Third+ consecutive same-tool call with no fresh finding → the result
    itself names the untouched coverage gaps and an alternative tool.

    The field bug: one good tool used all day ('AI gets a good result
    from one tool and uses it all day' — the operator's core-AI-bug
    complaint). Prompting alone didn't break it; the harness carries the
    signal on the result the model MUST read next."""
    try:
        trace = [t for t in (state or {}).get("execution_trace", []) if isinstance(t, dict)]
        tools = [str(t.get("tool", t.get("name", ""))) for t in trace[-3:]] + [tool_name]
        if len(tools) < 4 or not all(t == tool_name for t in tools[-4:]):
            return ""
        if "CONFIRMED" in output[:300] or "credential" in output[:300].lower():
            return ""  # progress exempts — matching the repeat-loop doctrine
        # what's untouched? the ledger knows
        assets = []
        for k in ("url", "target", "host"):
            v = str((tool_args or {}).get(k) or "").strip()
            if v:
                assets.append(v)
                break
        if not assets:
            assets = [str((state or {}).get("original_objective") or "")]
        gaps: list[dict] = []
        with contextlib.suppress(Exception):
            from suijin.modules.tools.lib.coverage import untested

            gaps = [g for g in untested(assets, limit=4) if int(g.get("weight", 0)) >= 5][:2]
        alt = ""
        with contextlib.suppress(Exception):
            from suijin.server.tools.lib.dispatch import _TOOL_ALTERNATIVES

            alt = ", ".join(_TOOL_ALTERNATIVES.get(tool_name, ()))
        lines = [
            f"[TUNNEL VISION — {tool_name} four+ calls in a row with no new finding. "
            "The tool is fine; the TARGET coverage isn't:"
        ]
        if gaps:
            for g in gaps:
                lines.append(f"  untested: {g['asset']} × {g['vuln_class']} (weight {g['weight']})")
        else:
            lines.append("  run coverage_check(action=list) for the priority gaps")
        if alt:
            lines.append(f"  alternative tools: {alt}")
        lines.append("  A confirmed primitive should open chains, not repeat the same probe.]")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — the notice never breaks execution
        return ""


def _state_result_message(tool_name, duration_ms, step_data, output, state) -> str:
    """The STATE-bound copy of a tool result — the context-cost contract:

    - DISTILLED (R1): raw results rode every turn's embed until compaction
      ate them; the full copy already lives in the trail/toollogs. Cap
      configurable via context_result_cap.
    - PIN-TAGGED (R2): findings/credentials enter with [PIN] so compaction
      carries them verbatim instead of compressing the crown jewels into
      an 80-char header line.
    """
    from suijin.modules.agent.lib.context_distill import distill_result

    cfg = (state or {}).get("_run_config") or {}
    cap = int(cfg.get("context_result_cap") or 0) or None
    body = distill_result(_wrap_untrusted(str(output), "TOOL_OUTPUT"), cap=cap)
    # SECRETS SURVIVE DISTILLATION: a credential deep in a long response
    # must stay in context (the bench's token chain was the canary).
    from suijin.modules.agent.lib.context_distill import extract_secret_excerpts

    secrets = extract_secret_excerpts(str(output or ""))
    if secrets:
        body += "\n[SECRET EXCERPTS — verbatim, never distilled]\n" + "\n".join(secrets)
    head = f"RESULT ({tool_name}, {duration_ms}ms, iteration {step_data.get('iteration', '?')}):"
    pin = ""
    out_l = str(output or "")
    if tool_name in ("catalog_exploit", "record_finding", "creds_add") or "CONFIRMED" in out_l[:200]:
        pin = "[PIN] "
    return f"{pin}{head}\n{body}"


def _audit_step(state, tool_name, tool_args, success, duration_ms):
    """Append the step to the engagement audit trail (never raises).

    H6: iteration comes from the EXECUTING STEP first — current_iteration
    isn't set in state at execute time, so every row in every
    agent_steps.jsonl read 'iteration=?' (per-iteration forensics were
    impossible)."""
    try:
        from suijin.kernel.audit import ToolAudit

        iteration = (state.get("_current_step") or {}).get("iteration") or state.get("current_iteration") or "?"
        ToolAudit(_artifact_dir("audit_trails"), "agent_steps.jsonl", flush_every=1).record(
            surface="agent",
            name=tool_name,
            args=tool_args,
            outcome="ok" if success else "tool-error",
            duration_ms=duration_ms,
            detail=f"iteration={iteration}",
        )
    except Exception:  # noqa: BLE001 — audit must never break execution
        pass
