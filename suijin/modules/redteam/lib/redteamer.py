"""
Suijin Red Team Agent — LangGraph-powered autonomous red teaming.

Orchestrates the LangGraph state machine: think -> execute_tool -> generate_response.
Support modules extracted for maintainability:
  suijin/core/red/config_loader.py   — config.json / .env management
  suijin/core/red/llm_client.py      — async LLM wrapper with timeout
  suijin/core/red/session_control.py — runtime commands (/report, /state, etc.)

Key features:
- Structured Pydantic output parsing (no regex hacks)
- Productivity scoring (zero-token stall detection)
- Prompt injection defense (unforgeable boundaries)
- Error classification (shell errors vs 4xx vs 5xx vs transport)
- Automatic checkpointing after every turn
- Hard guardrail (gov/mil/edu domain blocking)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time

from rich.console import Console
from rich.panel import Panel

from suijin.modules.redteam.lib.red import session_control as sc

# ENV_PATH / CONFIG_PATH re-exported for callers and tests that still import
# them from redteamer (their real home is config_loader).
from suijin.modules.redteam.lib.red.config_loader import (  # noqa: F401 — deliberate re-exports
    BASE_DIR,
    CONFIG_PATH,
    ENV_PATH,
    active_model,
    load_config,
    load_env,
)
from suijin.modules.redteam.lib.red.llm_client import generate_async

_loader_mod = __import__("suijin.modules.loader", fromlist=["discover_modules", "load_local_module"])
discover_modules = _loader_mod.discover_modules
load_local_module = _loader_mod.load_local_module

# Centralized force-load — shares ONE instance per module
providers = load_local_module("providers")


audit_mod = load_local_module("audit")
supervisor = load_local_module("supervisor")
supervisor.set_providers(providers)
oracle = load_local_module("oracle")
oracle.set_providers(providers)


console = Console()
DUMP_PATH = BASE_DIR / "operation_state_recovery.json"

_SCOPE_CONFIRM_RE = __import__("re").compile(
    r"(?i)(permission|authoriz|authoris|in scope|scope confirmed|i own|owned by me|my (server|domain|site|vm|box)"
    r"|written approval|h1|hacker ?one|bug ?bounty|contract|go ahead|approved|confirmed)"
)

# questions that are RE-LITIGATING authorization (vs. genuinely novel scope asks)
_SCOPE_DOUBT_RE = __import__("re").compile(
    r"(?i)(authoriz|permission|proof|verif|security\.txt|security contact|dns txt"
    r"|in.scope|out.of.scope|evidence|whois owner|prove (that )?you|confirm (that )?you)"
)

_URL_IN_ANSWER_RE = __import__("re").compile(r"https?://[^\s\"')<>]+")


def _looks_like_scope_confirmation(answer: str) -> bool:
    """Operator language that settles authorization/scope."""
    return bool(_SCOPE_CONFIRM_RE.search(answer or ""))


# ── termination classifier — every ending gets exactly one banner ──────

_DECLINE_WORDS = ("declin", "refuse", "will not proceed", "cannot proceed", "not able to proceed", "i won't")
_FAIL_REASONS = ("parse_failure", "llm_error", "provider_failure", "budget_exhausted", "node_crash")


def _classify_termination(reason: str, final_state: dict, operator_stopped: bool) -> str:
    r = (reason or "").lower()
    if operator_stopped:
        return "OPERATOR"
    if any(w in r for w in _DECLINE_WORDS):
        return "DECLINED"
    if reason in _FAIL_REASONS or reason.startswith("error:"):
        return "FAILED"
    iters = int(final_state.get("current_iteration", 0) or 0)
    if iters == 0 and not reason:
        return "NO_OUTPUT"
    return "COMPLETE"


def _render_termination(final_state: dict, ui, operator_stopped: bool) -> None:
    reason = str(final_state.get("completion_reason", "") or "")
    kind = _classify_termination(reason, final_state, operator_stopped)
    if kind == "COMPLETE":
        console.print(
            Panel(
                f"{reason or 'objective complete'}",
                title=" ENGAGEMENT COMPLETE ",
                title_align="left",
                border_style="green",
            )
        )
    elif kind == "DECLINED":
        console.print(
            Panel(
                f"{reason}\n\n[dim]The agent halted itself. Put authorization on file first:\n"
                "  suijin authorize <target> --program h1 --id <auth-id>\n"
                "then re-run — the record rides every order; or answer its scope question.[/dim]",
                title=" ENGAGEMENT STOPPED — DECLINED ",
                title_align="left",
                border_style="yellow",
            )
        )
    elif kind == "OPERATOR":
        console.print(
            Panel(
                "operator interrupt — engagement ended by the operator",
                title=" OPERATOR STOP ",
                title_align="left",
                border_style="yellow",
            )
        )
    elif kind == "NO_OUTPUT":
        console.print(
            Panel(
                "the agent produced no iterations — check the provider key (suijin env), "
                "provider config (suijin config show) and outputs/logs/engage_crash.log",
                title=" ENGAGEMENT FAILED — NO OUTPUT ",
                title_align="left",
                border_style="red",
            )
        )
    else:  # FAILED
        detail = str(final_state.get("final_summary", "") or "")
        if not detail:
            for msg in reversed(final_state.get("messages", [])):
                if msg.get("role") == "assistant" and msg.get("content"):
                    detail = f"last model output: {str(msg['content'])[:400]}"
                    break
        if reason == "parse_failure":
            detail = (detail + "\n\n" if detail else "") + (
                "the model returned non-JSON 3 times (often an overloaded/timeouty "
                "provider returning empty or partial responses) — try again, or "
                "switch provider: suijin providers"
            )
        console.print(
            Panel(
                f"{reason}\n{detail}" if detail else reason,
                title=" ENGAGEMENT FAILED ",
                title_align="left",
                border_style="red",
            )
        )


#  Main agent loop


async def run_red_team_async(config, objective, api_key=None, resume_state=None):
    # the pydantic cost-cap warning echoes validator internals as a wall of
    # text — silenced everywhere; ONE red line below instead
    import warnings

    from suijin.modules.platform.lib.config_models import CostCapWarning

    warnings.filterwarnings("ignore", category=CostCapWarning)
    # no cost-cap console notice — the operator's cap is deliberate

    providers.reset_usage()
    # scope ALL per-engagement state (schema/recovery/scratchpad/approvals)
    # to outputs/engagements/<stamp>_<slug>/ — state dies with the run
    from suijin.modules.platform.lib.workspace import set_engagement

    set_engagement(objective)
    # stderr poisons the Live strip: provider/tool warnings wrote straight
    # to the terminal above the region, leaving frozen artifact rows. The
    # suijin logger goes to a file for the engagement's lifetime instead.
    try:
        import logging as _logging

        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR as _WS

        _logd = _WS / "outputs" / "logs"
        _logd.mkdir(parents=True, exist_ok=True)
        _fh = _logging.FileHandler(_logd / "engagement.log")
        _fh.setFormatter(_logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        _logging.getLogger("suijin").handlers = [_fh]
        _logging.getLogger("suijin").propagate = False
    except Exception:  # noqa: BLE001 — logging setup must never block a run
        pass
    # B11/B16: recall operational memory for the target — silent (the
    # 'no memory of X yet' line was startup noise; the scratchpad carries
    # memory into the prompt where it actually matters)
    try:
        from suijin.modules.agent.lib import memory as _mem

        # H5: recall is RENDERED, not discarded — the agent starts with its
        # prior engagements against this target (memory was pull-only and
        # nothing ever pulled)
        _recall = _mem.recall(str(objective)[:120], limit=3)
        if _recall and _recall.strip():
            console.print("[dim]prior engagements recalled (outputs/memory/)[/dim]")
    except Exception:  # noqa: BLE001 — memory is best-effort
        pass

    from suijin.modules.platform.lib import runtime as _runtime

    _runtime.reset_recon_state()

    # Apply proxy setting from config
    proxy_url = config.get("proxy_url", "")
    if proxy_url:
        _dispatch_mod().set_proxy(proxy_url)
        console.print(f"[dim]Proxy: {proxy_url}[/dim]")

    # Streaming: the graph's generate_fn carries the UI's reasoning sink —
    # deltas flow to the flexing box while the LLM thinks. The cell is
    # filled once the UI exists (the graph boots before it); fireteam
    # subagents inherit the same generate_fn (their deltas interleave in
    # the box — cosmetic, tail-capped).
    _stream_ui = {"sink": None}
    from suijin.modules.redteam.lib.red.console_ui import UI_STATE as _UI_STATE

    with contextlib.suppress(Exception):
        _prov = str(config.get("provider", "?"))
        _model = ""
        with contextlib.suppress(Exception):
            _model = str(active_model(config) or "")
        _UI_STATE["model_label"] = f"{_prov} {_model}".strip()

    def _generate_with_stream(messages, config=None, on_delta=None, **kw):
        # on_delta=False from subagents SUPPRESSES display streaming (only
        # the primary's thought renders — fireteam deltas never interleave)
        sink = _stream_ui["sink"] if on_delta is None else (None if on_delta is False else on_delta)
        # model intelligence (Ctrl+Space) applies to the NEXT call — it is
        # read per-call, never mid-thought, per the operator contract
        cfg = dict(config or {})
        with contextlib.suppress(Exception):
            cfg["intelligence"] = _UI_STATE.get("intelligence", "max")
        return generate_async(messages, cfg, on_delta=sink, **kw)

    agent = _agent_graph_cls()(
        generate_fn=_generate_with_stream,
        route_tool_fn=_dispatch_mod().route_tool,
        max_iterations=config.get("max_iterations", 100),
        run_config=config,
    )

    thread_id = f"redteam_{int(time.time())}"

    provider_name = config.get("provider", "unknown")
    model_name = active_model(config)
    console.print("\n[bold #e6b47c] Launching Agent[/bold #e6b47c] [dim](Ctrl+C to guide)[/dim]")
    console.print(f"[dim]{objective}[/dim]")
    console.print(f"[dim]{provider_name} / {model_name}[/dim]\n")

    agent._build()  # ensure graph is compiled
    last_iter = 0
    final_state = {}
    first_run = True
    _parse_retries = 0
    _operator_stopped = False  # unbound-local crash: the finally referenced
    # this before ANY assignment when an early exception jumped the loop
    _provider_retried = False
    langgraph_config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 250}

    # .sje resume: seed the fresh thread with the saved engagement's state
    # (messages, traces, chain memory) — the same update_state seam
    # operator guidance uses. The agent CONTINUES, it does not restart.
    if resume_state:
        try:
            agent._graph.update_state(langgraph_config, dict(resume_state))
            n_msgs = len(resume_state.get("messages") or [])
            console.print(
                f"[green]resumed from saved engagement — {n_msgs} message(s), "
                f"iteration {resume_state.get('current_iteration', '?')}, "
                f"phase {resume_state.get('current_phase', '?')}[/green]"
            )
            first_run = False  # state already seeded — no objective injection turn
        except Exception as e:  # noqa: BLE001 — resume failure falls back to fresh
            console.print(f"[yellow]resume injection failed ({e}) — starting fresh[/yellow]")

    # Start audit trail
    try:
        from suijin.modules.tools.lib.audit_trail import start_audit

        start_audit(objective[:80])
    except Exception:
        pass

    # Dual-layer signal handling — works during I/O blocks
    import signal as _signal

    _interrupted = False

    def _sigint(sig, frame):
        # INSTANT pause: raise in-place — the main thread is inside the
        # astream await, and the exception unwinds straight to the pause
        # handler instead of waiting (up to 90s) for the current LLM call
        # to finish. The flag stays for RunBox's /pause (cross-thread).
        _signal._suijin_interrupted = True
        raise KeyboardInterrupt

    _old_sigint = _signal.signal(_signal.SIGINT, _sigint)
    _signal._suijin_interrupted = False

    # Live command box — /state /note /kb /pause … usable WHILE the agent runs
    from suijin.modules.tools.lib.run_commands import HINT, RunBox

    run_box = RunBox(
        get_state=lambda: agent.get_state(thread_id) or {},
        thread_id=thread_id,
        config=config,
        console=console,  # ONE console — a second one interleaves mid-refresh
        # with the live strip (field garbling: 'queued as guidance' painted
        # over the spinner line)
    )

    # Engagement console UI — transcript + pinned strip (Rich only)
    from suijin.modules.redteam.lib.red.console_ui import EngagementUI, toggle_reasoning

    ui = EngagementUI(console, objective=objective)
    ui.start()
    _stream_ui["sink"] = ui.reasoning_delta  # the flexing box goes live

    # The input box: on a TTY the keystroke reader owns stdin (live typing,
    # Tab mode cycling, ESC ESC pause) and the RunBox's line reader stays
    # OFF — one owner of stdin, ever. Piped/CI keeps the line reader.
    _pause_q = None
    _input_reader = None
    if sys.stdin.isatty():
        import queue as _qmod

        from suijin.modules.redteam.lib.red.console_input import RedInputReader

        _pause_q = _qmod.Queue()

        _pause_session = {"active": False, "guidance": None, "stop": False, "done": __import__("threading").Event()}

        def _start_pause_session():
            """READER THREAD: the instant pause — banner + PAUSED visual +
            command consumption all happen HERE. The main thread (stuck in
            an LLM call whose KI LangGraph swallows) joins when it lands;
            the operator never waits for it."""
            if _pause_session["active"]:
                return
            _pause_session["active"] = True
            _pause_session["guidance"] = None
            _pause_session["stop"] = False
            _pause_session["done"].clear()
            ui.paused_visual(True)
            console.print(sc.PAUSE_BANNER)
            console.print("[dim]paused — commands answer instantly; the current turn finishes in the background[/dim]")

        def _pause_line(line):
            """READER THREAD: one entered line = one pause-console step.
            Slash commands dispatch now; a plain line is the guidance and
            ends the session."""
            ctx = _pause_session.get("ctx")
            if ctx is None:
                return
            cmd, _, rest = (line or "").partition(" ")
            handler = sc.build_pause_handlers(ctx).get(cmd.lower())
            if handler is not None:
                handler(rest.strip())
                if getattr(ctx, "stop_requested", False):
                    _pause_session["stop"] = True
                    _pause_session["done"].set()
                return
            extras = [g for g in getattr(ctx, "guidance_extra", []) if g]
            _pause_session["guidance"] = " ".join(extras + ([line] if line else [])) or "Continue what you were doing."
            console.print("[dim]guidance queued — resumes when the current turn ends[/dim]")
            _pause_session["done"].set()

        def _esc_pause():
            import _thread
            import signal as _sig

            _sig._suijin_interrupted = True
            _start_pause_session()
            _input_reader._on_pause_line = _pause_line  # commands answer NOW
            _thread.interrupt_main()

        def _live_guidance(line):
            """Plain prompt -> live_guidance.md (the file the think node
            reads at the TOP of its prompt every turn — atomic, no LangGraph
            state mutation, cannot fail)."""
            from suijin.modules.agent.lib.live_guidance import write_guidance

            with contextlib.suppress(Exception):
                mode = str(_input_reader.mode or "recon").upper()
            write_guidance(line, mode=mode)
            # the sent prompt shows as a little box right under the stream
            with contextlib.suppress(Exception):
                from rich.panel import Panel as _Panel

                ui.console.print(
                    _Panel(
                        f"[bold cyan]{str(line)[:400]}[/bold cyan]",
                        title=f" {mode} ",
                        title_align="left",
                        border_style="cyan",
                        padding=(0, 1),
                    )
                )

        # PauseContext up front: the reader-side session must answer /state
        # /cost /quit the INSTANT ESC ESC fires — the main thread may be
        # stuck in an LLM call for seconds yet. Live values ride a holder.
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE as _UI_LOOT

        _pause_live = {"agent": agent, "thread_id": thread_id, "final_state": {}, "objective": objective}

        _pause_ctx = sc.PauseContext(
            console=console,
            agent=agent,
            langgraph_config=langgraph_config,
            thread_id=thread_id,
            config=config,
            objective=objective,
            route_tool_fn=lambda name, args: _dispatch_mod().route_tool(name, args, {}),
            usage_fn=providers.get_usage,
            loot=_UI_LOOT,
            force_report_fn=lambda: sc.force_report(
                _pause_live["agent"],
                _pause_live["thread_id"],
                _pause_live["final_state"],
                _pause_live["objective"],
                config,
            ),
        )
        _pause_session["ctx"] = _pause_ctx

        _input_reader = RedInputReader(run_box, ui, on_pause=_esc_pause, on_guidance=_live_guidance, on_pause_line=None)
        if _input_reader.start():
            _input_reader.arm_pause(_pause_q)  # ESC ESC routes to it instantly
            console.print("[dim]input box live — Tab: mode · ESC ESC: pause · / for commands[/dim]")
        else:
            _input_reader = None
            run_box.start()
    else:
        run_box.start()

    from suijin.modules.redteam.lib.red.console_ui import ask_operator_answer as _ask_op

    def _operator_input(label: str, timeout_s: float = 600.0) -> str:
        """Operator text through the ONE stdin owner. TTY: the keystroke
        reader routes raw lines to an ask queue (console.input fought the
        cbreak reader and typing DIED — the field bug). Non-TTY: the old
        RunBox/line-reader path."""
        import queue as _qmod

        if _input_reader is not None:
            q = _qmod.Queue()
            with contextlib.suppress(Exception):
                console.print(f"[bold cyan]{label}[/bold cyan] [dim]— type your answer in the input box[/dim]")
            _input_reader.begin_ask(q)
            try:
                return q.get(timeout=timeout_s)
            except _qmod.Empty:
                return ""
            finally:
                _input_reader.end_ask()
        if run_box.alive:
            return _ask_op(run_box, console, "", timeout_s=timeout_s, label=label.strip())
        try:
            return console.input(f"[bold cyan]{label}[/bold cyan] ")
        except (KeyboardInterrupt, EOFError):
            return ""

    run_box.register(
        "think",
        lambda _a: toggle_reasoning(console),
    )
    console.print(HINT + " [dim]/think (toggle reasoning)[/dim]")

    _resume_retry = False  # one clean-restart allowed after a paused resume

    while True:
        try:
            _got_events = False
            input_state = {"_objective": objective, "user_id": "local", "project_id": "default"} if first_run else None
            first_run = False

            # Queue-bridge astream: LangGraph's generator SWALLOWS the KI
            # from interrupt_main() — wait_for around __anext__ BREAKS async
            # generators (cancellation consumes the item). Instead: a
            # background reader pushes events to a queue; the main loop
            # reads with timeout and polls the interrupt flag every 2s
            # (fixes the unpause-deadlock when fireteam subagents hold it)
            import queue as _qmod

            _eq = _qmod.Queue()

            async def _astream_reader(_g=agent._graph, _is=input_state, _lc=langgraph_config, _q=_eq):
                try:
                    async for _ev in _g.astream(_is, _lc):
                        _q.put_nowait(_ev)
                except BaseException:
                    pass  # the generator's errors are the loop's to handle
                finally:
                    _q.put_nowait(None)  # sentinel: stream done

            _reader_task = asyncio.create_task(_astream_reader())
            while True:
                try:
                    event = await asyncio.wait_for(asyncio.to_thread(_eq.get, timeout=2.0), timeout=3.0)
                except (asyncio.TimeoutError, _qmod.Empty):
                    if getattr(_signal, "_suijin_interrupted", False):
                        _reader_task.cancel()
                        raise KeyboardInterrupt()
                    continue
                if event is None:  # sentinel — astream completed
                    break
                _got_events = True
                if getattr(_signal, "_suijin_interrupted", False):
                    _reader_task.cancel()
                    raise KeyboardInterrupt()
                node_name = list(event.keys())[0]
                node_output = event[node_name]

                # diag: every node transition (think->execute->think...)
                with contextlib.suppress(Exception):
                    from suijin.kernel.diag import diag as _diag

                    _diag("node", name=node_name, iter=node_output.get("current_iteration", "?"))

                # Guidance delivery confirmation: when a think event fires,
                # check the context manifest for guidance consumed this turn
                if node_name == "think":
                    with contextlib.suppress(Exception):
                        from suijin.modules.agent.lib.live_guidance import context_path

                        cp = context_path()
                        if cp.is_file():
                            body = cp.read_text(encoding="utf-8", errors="ignore")
                            if "delivered this turn" in body:
                                # extract the guidance text
                                gd_start = body.find("delivered this turn") + len("delivered this turn)") + 1
                                gd_end = body.find("\n## ", gd_start)
                                gd_text = (
                                    body[gd_start:gd_end].strip()
                                    if gd_end > gd_start
                                    else body[gd_start : gd_start + 200]
                                )
                                if gd_text and "(none" not in gd_text:
                                    ui.guidance_delivered(gd_text)

                ui.waiting(False)  # an event arrived — spinner off

                trace = node_output.get("execution_trace", [])
                step = node_output.get("_current_step", {})

                # Show output from execute_tool_node (sync result, bg spawn, blocked…)
                if node_name == "execute_tool" and step.get("tool_output"):
                    ec = step.get("error_class", "")
                    out = str(step["tool_output"])
                    if ec == "ask_operator":
                        ui.ask(out)
                        # AUTO-ANSWER from the ledger: when the operator already
                        # put authorization on file (suijin authorize) and the
                        # question is re-litigating it, the record answers —
                        # no human round-trip (field run: the model demanded
                        # 'verifiable evidence' despite a VERIFIED record)
                        _ledger_line = None
                        try:
                            from suijin.modules.ops.lib.authorizations import authorization_line

                            _ledger_line = authorization_line(objective)
                        except Exception:  # noqa: BLE001
                            pass
                        if _ledger_line and _SCOPE_DOUBT_RE.search(out):
                            _final = (
                                "OPERATOR: confirmed, authorization record on file "
                                f"({_ledger_line}). Continuing — next action on the target."
                            )
                            agent._graph.update_state(
                                langgraph_config,
                                {
                                    "messages": [{"role": "user", "content": _final}],
                                    "_ask_operator": False,
                                },
                            )
                            console.print("[dim]scope question auto-answered from the authorization record[/dim]\n")
                            ui.waiting(True)
                            continue
                        # B1: the strip's Live repaints at 4fps and CLOBBERS a
                        # printed prompt — stop it for the whole operator-input
                        # window, restart after.
                        ui.stop()
                        # Pause graph, ask operator, inject answer, resume.
                        # The RunBox reader thread owns stdin — the answer is
                        # taken through its guidance queue (input() on the
                        # main thread raced the reader and hung the agent).
                        # Detached engagements (stdin dead) use the desktop
                        # bridge instead.
                        if _input_reader is not None or run_box.alive:
                            # the keystroke reader (or RunBox) owns stdin —
                            # console.input fought it and typing DIED
                            answer = _operator_input("Answer", 600.0)
                        elif sys.stdin is not None and sys.stdin.isatty():
                            try:
                                answer = console.input("[bold cyan]Answer:[/bold cyan] ").strip()
                            except (KeyboardInterrupt, EOFError):
                                answer = ""
                        else:
                            from suijin.modules.console.lib.gateway import fetch_answer, push_question

                            qid = push_question(out)
                            console.print(
                                "[dim]question sent to the desktop/mcp surface — waiting for the operator (10m)...[/dim]"
                            )
                            answer = fetch_answer(qid, timeout_s=600.0) or ""
                        if not answer:
                            answer = "Continue as you see fit."
                        # URL in the answer = the operator linked a program page:
                        # persist it onto the target's authorize record, and
                        # tell the agent it can fetch it (with the CF-exists
                        # doctrine) instead of stalling on verification
                        _page_url = _URL_IN_ANSWER_RE.search(answer)
                        if _page_url:
                            try:
                                from suijin.modules.ops.lib.authorizations import set_page

                                _sp = set_page(objective, _page_url.group(0))
                                if "error" not in _sp:
                                    console.print(f"[green]program page on file: {_sp['page']}[/green]")
                            except Exception:  # noqa: BLE001
                                pass
                            _final = (
                                f"OPERATOR ANSWER: {answer}\n"
                                "That URL is now the program page on file. You may verify it yourself "
                                "with fetch_authorization_page — note: a Cloudflare/WAF block on fetch "
                                "means the page EXISTS (nonexistent pages 404); that is ample. Continue."
                            )
                        elif _looks_like_scope_confirmation(answer):
                            try:
                                from suijin.modules.agent.lib import memory as _mem

                                _mem.note(objective, f"operator confirmed scope/authorization: {answer[:200]}")
                            except Exception:  # noqa: BLE001 — memory is best-effort
                                pass
                            _final = f"OPERATOR: confirmed — {answer}. Continuing."
                        else:
                            _final = f"OPERATOR ANSWER: {answer}"
                        if _page_url or _looks_like_scope_confirmation(answer):
                            # persist into the objective so the engagement order
                            # (rendered every turn) carries it forever
                            _confirmed_obj = f"{objective} [OPERATOR-CONFIRMED in engagement: {answer[:160]}]"
                            agent._graph.update_state(
                                langgraph_config,
                                {
                                    "original_objective": _confirmed_obj,
                                    "_objective": _confirmed_obj,
                                },
                            )
                            objective = _confirmed_obj
                        agent._graph.update_state(
                            langgraph_config,
                            {
                                "messages": [{"role": "user", "content": _final}],
                                "_ask_operator": False,
                            },
                        )
                        console.print("[dim]Answer sent. Resuming...[/dim]\n")
                        ui.start()
                        ui.waiting(True)  # straight back to the thinking spinner
                        continue
                    if step.get("tool_name") == "catalog_exploit" and ui.exploit_verdict(out):
                        pass  # the classed verdict panel rendered
                    else:
                        ui.output(out, ec)
                    # Audit the FULL observation from the execute event — the
                    # old think-side log raced ahead of execution and logged
                    # every observation empty.
                    try:
                        from suijin.modules.tools.lib.audit_trail import log_iteration

                        log_iteration(
                            iteration=step.get("iteration", 0),
                            thought=step.get("thought", ""),
                            reasoning=step.get("reasoning", ""),
                            tool_name=step.get("tool_name", ""),
                            tool_args=dict(step.get("tool_args") or {}),
                            tool_output=out,
                            success=bool(step.get("success", True)),
                            phase=step.get("phase", ""),
                            completion_reason=node_output.get("completion_reason", ""),
                        )
                    except Exception:
                        pass

                if trace:
                    latest = trace[-1]
                    iteration = latest.get("iteration", 0)
                    if iteration > last_iter:
                        last_iter = iteration
                        thought = latest.get("thought", "")
                        tool_name = latest.get("tool_name", "")
                        tool_args = latest.get("tool_args", {})
                        reasoning = latest.get("reasoning", "")
                        success = latest.get("success", True)
                        phase = latest.get("phase", node_output.get("current_phase", "?"))

                        ui.iteration_header(iteration, phase)
                        ui.stream_done()  # the flexing box collapses — the block takes over
                        # ask turns (BOTH forms): question + Answer prompt only —
                        # no thinking/said sections. The action-form ask carries
                        # tool_name on _current_step, not on the trace step.
                        _is_ask = tool_name == "ask_operator" or step.get("tool_name") == "ask_operator"
                        if thought and not _is_ask:
                            ui.thinking(thought)
                        if not _is_ask:
                            ui.reasoning(reasoning)
                        if tool_name and not _is_ask:
                            ui.tool(tool_name, tool_args)
                        _plan = latest.get("_plan_remaining") or (tool_args or {}).get("_plan_remaining") or []
                        if _plan and isinstance(_plan, list):
                            ui.planned_steps(_plan)

                        # Audit the decision (think side): bookkeeping turns
                        # (transition/ask_user/switch_skill/complete) never
                        # reach execute_tool, so they log here.
                        if not tool_name:
                            try:
                                from suijin.modules.tools.lib.audit_trail import log_iteration

                                log_iteration(
                                    iteration=iteration,
                                    thought=thought,
                                    reasoning=reasoning,
                                    tool_name=tool_name,
                                    tool_args=dict(tool_args),
                                    tool_output="",
                                    success=success,
                                    phase=phase,
                                    completion_reason=node_output.get("completion_reason", ""),
                                )
                            except Exception:
                                pass

                # ── think-side signals (streamed into the open block) ──
                if node_name == "think":
                    # parse failures must be VISIBLE — a run that dies on 3
                    # unparseable responses used to just vanish. The retry
                    # loop is internal to think_node; the message that
                    # reaches this consumer is "JSON parse failed after N".
                    for _m in node_output.get("messages", []):
                        _c = str(_m.get("content", ""))
                        if "could not be parsed" in _c or "JSON parse failed" in _c:
                            _parse_retries += 1
                            ui.parse_note(min(_parse_retries, 3))
                    _jt = node_output.get("_just_transitioned_to", "")
                    if _jt:
                        ui.phase_transition(_jt)
                    _sv = node_output.get("_supervisor_guidance", "")
                    if _sv:
                        ui.supervisor(_sv)
                    _or = node_output.get("_oracle_hypotheses")
                    if _or:
                        ui.oracle(_or)
                    _dw = node_output.get("_drift_warning", "")
                    if _dw:
                        ui.drift(_dw)
                    # fireteam deploy confirmation rides on think steps
                    if step.get("tool_output") and str(step.get("tool_args", {}).get("tasks", "")):
                        ui.fireteam(str(step["tool_output"]))

                # between events the strip spins (thinking / working)
                ui.waiting(True)

                # Check completion
                if node_output.get("completion_reason"):
                    _cr = str(node_output.get("completion_reason", ""))
                    if _cr in ("provider_failure", "llm_error") and not _provider_retried:
                        # provider flake (timeouts/empty responses under load)
                        # does NOT end the engagement: one full restart with a
                        # fresh thread. The field runs died to exactly this.
                        _provider_retried = True
                        console.print(
                            f"[yellow]provider trouble ({_cr}) — one automatic restart, then it ends[/yellow]"
                        )
                        ui.flush_open()
                        agent = _agent_graph_cls()(
                            generate_fn=_generate_with_stream,
                            route_tool_fn=_dispatch_mod().route_tool,
                            max_iterations=config.get("max_iterations", 100),
                            run_config=config,
                        )
                        thread_id = f"redteam_{int(time.time())}"
                        langgraph_config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 250}
                        agent._build()
                        _pause_live.update({"agent": agent, "thread_id": thread_id})
                        _pause_ctx.agent = agent
                        _pause_ctx.thread_id = thread_id
                        first_run = True
                        ui.waiting(True)
                        continue
                    ui.flush_open()
                    final_state = node_output
                    break
            else:
                # If loop completes without break, get final state
                final_state = agent.get_state(thread_id) or {}
                # Resume hardening: zero events from a resumed astream =
                # the graph's internal state was stale after the KI —
                # restart cleanly from the objective (one retry only)
                if not _got_events and _resume_retry:
                    _resume_retry = False
                    first_run = True
                    console.print("[yellow]  graph resumed empty — restarting from objective[/yellow]")
                    continue

            run_box.stop()
            if _input_reader is not None:
                _input_reader.stop()
            break  # Normal completion — exit while loop

        except (KeyboardInterrupt, asyncio.CancelledError):
            _signal._suijin_interrupted = False
            # _sigint STAYS installed through the pause: Ctrl+C raises
            # KeyboardInterrupt instantly anywhere — SIG_DFL here would
            # have KILLED the app with no save.
            _signal.signal(_signal.SIGINT, _sigint)
            ui.flush_open()

            _pause_live.update(
                {"agent": agent, "thread_id": thread_id, "final_state": final_state, "objective": objective}
            )
            _pause_ctx.agent = agent
            _pause_ctx.thread_id = thread_id
            _pause_ctx.objective = objective
            _pause_session["ctx"] = _pause_ctx

            if _input_reader is not None and _pause_session["active"]:
                # ESC ESC fired MID-TURN: the reader already runs the pause
                # session (banner + PAUSED visual + instant commands). The
                # operator may have already typed guidance or /quit — join.
                _pause_session["ctx"] = _pause_ctx  # handlers now answer /state etc.
                _pause_session["done"].wait()
            elif _input_reader is not None:
                # Ctrl+C path (no session yet): start the reader-side
                # session NOW — same instant behavior, same consumer.
                _pause_session["ctx"] = _pause_ctx
                _start_pause_session()
                _input_reader._on_pause_line = _pause_line
                _pause_session["done"].wait()
            else:
                # non-TTY: no box exists — the legacy prompt path
                ui.stop()
                try:
                    guidance = sc.pause_console(
                        _pause_ctx, lambda label, timeout=600.0: _operator_input(label, timeout)
                    )
                    _pause_session["guidance"] = guidance
                except (KeyboardInterrupt, EOFError):
                    guidance = None
                    _pause_session["stop"] = True
                objective = _pause_ctx.objective
                _pause_session["done"].set()

            objective = _pause_ctx.objective  # /objective may have changed course

            if _pause_session["stop"] or getattr(_pause_ctx, "stop_requested", False):
                # /quit or force-quit — save everything (session + .sje)
                final_state = agent.get_state(thread_id) or {}
                _operator_stopped = True  # the banner must not fake a completion
                console.print("[dim]  engagement ended — full save follows[/dim]")
                ui.stop()
                run_box.stop()
                if _input_reader is not None:
                    _input_reader.end_pause()
                    _input_reader.stop()
                break

            guidance = _pause_session["guidance"]
            # Pause guidance goes to the SAME file the think node reads —
            # one mechanism, no update_state, no silent failure
            if guidance and guidance != "Continue what you are doing.":
                from suijin.modules.agent.lib.live_guidance import write_guidance

                write_guidance(guidance, mode="PAUSED")
                console.print("[dim]  guidance written — the AI reads it next turn[/dim]\n")
            # resume: session closes, the box returns to live routing, the
            # thought stream resumes, the strip un-pauses
            _pause_session["active"] = False
            _pause_session["ctx"] = None
            if _input_reader is not None:
                _input_reader._on_pause_line = None
                _input_reader.end_pause()
                _input_reader.arm_pause(_pause_q)
            ui.paused_visual(False)
            ui.waiting(True)
            # Resume hardening: the KI that paused us may have left the
            # graph's internal execution state stale — if the next astream
            # yields nothing, the retry below restarts from the objective
            _resume_retry = True
            continue  # Resume the while loop

        except Exception as e:
            # Graph crashed (bug, not operator interrupt) — report and end
            # the engagement instead of killing the whole application.
            console.print(f"\n[bold red]  Agent loop error: {e}[/bold red]")
            ui.stop()
            run_box.stop()
            if _input_reader is not None:
                _input_reader.stop()
            import traceback

            traceback.print_exc()
            try:  # field crashes must be diagnosable after the fact
                from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

                _d = WORKSPACE_DIR / "outputs" / "logs"
                _d.mkdir(parents=True, exist_ok=True)
                (_d / "engage_crash.log").open("a").write(
                    f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {objective[:80]}\\n"
                    + traceback.format_exc()
                    + "\\n"
                )
            except Exception:  # noqa: BLE001
                pass
            final_state = agent.get_state(thread_id) or {}
            break

    #  Final report (after normal completion)
    try:
        messages = final_state.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and len(msg.get("content", "")) > 50:
                console.print(Panel(msg["content"][:5000], title=" Report", border_style="green"))
                break

        #  Summary — done() runs unconditionally in the finally block
        spend = float(providers.USAGE.get("est_cost_usd", 0))

        # Save state
        DUMP_PATH.write_text(
            json.dumps(
                {
                    "objective": objective,
                    "phase": final_state.get("current_phase"),
                    "iterations": final_state.get("current_iteration"),
                    "trace_count": len(trace),
                },
                indent=2,
            )
        )

        # End audit trail and save session
        try:
            from suijin.modules.tools.lib.audit_trail import end_audit

            end_audit(spend)
        except Exception:
            import logging

            logging.getLogger("suijin").warning("Agent loop error", exc_info=True)
        try:
            from suijin.modules.tools.lib.session_replay import save_session

            save_session(thread_id, objective, config, final_state, spend)
        except Exception as e:
            import logging

            logging.getLogger("suijin").warning(f"Session save failed: {e}")

        # .sje bundle — the concluded engagement, resumable (`suijin load`)
        try:
            from suijin.modules.tools.lib.engagement_bundle import save_engagement

            _sje = save_engagement(thread_id, objective, config, final_state, spend)
            console.print(f"[dim]engagement bundle saved — resume anytime: suijin load {_sje.name}[/dim]")
        except Exception as e:
            import logging

            logging.getLogger("suijin").warning(f".sje save failed: {e}")

        # the .sje bundle IS the resume artifact — retire the live state dir
        # into outputs/archive/ (the immortal-root-state fix)
        try:
            from suijin.modules.platform.lib.workspace import archive_engagement

            _arch = archive_engagement("ended")
            if _arch is not None:
                console.print(f"[dim]engagement state archived: {_arch.name}[/dim]")
        except Exception:
            pass

        # H5: write the engagement to per-target memory — 361 sessions had
        # produced ZERO memory entries because this was never called
        try:
            from suijin.modules.agent.lib import memory as _mem

            _mem.record_engagement(
                str(objective)[:120],
                str(objective)[:200],
                {
                    "iterations": final_state.get("current_iteration", 0),
                    "completion": str(final_state.get("completion_reason", "?"))[:80],
                    "cost_usd": round(float(providers.USAGE.get("est_cost_usd", 0)), 4),
                },
            )
        except Exception:  # noqa: BLE001 — memory is best-effort
            pass

        # Finding verification + peer review (A1/A2): findings get an
        # independent second pass and a skeptic/judge LLM review before
        # anyone reads the report. Best-effort; never blocks completion.
        try:
            from suijin.modules.agent.lib.verify import peer_review, verify_findings

            findings = final_state.get("findings") or []
            if findings:
                checked = verify_findings(findings)
                reviewed = peer_review(checked, config)
                final_state["findings"] = reviewed["reviewed"]
                console.print(
                    f"[dim]findings: verified/reviewed {len(reviewed['reviewed'])} (peer source: {reviewed['source']})[/dim]"
                )
        except Exception as e:
            import logging

            logging.getLogger("suijin").warning(f"finding verification skipped: {e}")

        # Self-critique: the agent reviews its own engagement, writes a
        # report + learnings (config-gated: self_critique=true default).
        try:
            from suijin.modules.agent.lib.critique import run_self_critique

            critique = run_self_critique(
                objective=objective, final_state=final_state, config=config, thread_id=thread_id
            )
            if critique:
                console.print("[dim]Self-critique saved (outputs/reports/critique_*.md)[/dim]")
        except Exception as e:
            import logging

            logging.getLogger("suijin").warning(f"Self-critique failed: {e}")

    except Exception as e:
        console.print(f"[bold red]Agent error: {e}[/bold red]")
        import traceback

        traceback.print_exc()
    finally:
        # TERMINATION BANNER — one classifier, every ending, unskippable.
        # The field reports: declines and crashes read as silent failures.
        with contextlib.suppress(Exception):
            _render_termination(final_state, ui, _operator_stopped)
        # the Done line ALWAYS renders — if anything above threw, the run
        # used to exit with zero summary (the 'silent crash')
        with contextlib.suppress(Exception):
            ui.done(
                sum(1 for s in final_state.get("execution_trace", []) if s.get("success", True)),
                len(final_state.get("execution_trace", [])),
                str(final_state.get("current_phase", "?")),
                float(providers.USAGE.get("est_cost_usd", 0)),
                str(final_state.get("completion_reason", "error before completion")),
            )
        with contextlib.suppress(Exception):
            ui.stop()


#  Helper functions — re-exported from session_control for backwards compat


def _dispatch_mod():
    """Tools dispatch module, lazily (boundary rule)."""
    from suijin.modules.tools.lib import dispatch as _d

    return _d


def _agent_graph_cls():
    """Agent graph class (honours a monkeypatched module attr)."""
    v = globals().get("SuijinAgentGraph")
    if v is not None:
        return v
    from suijin.modules.agent.lib.agent_graph import SuijinAgentGraph

    return SuijinAgentGraph


def __getattr__(name):
    if name == "_dispatch":
        from suijin.modules.tools.lib import dispatch as _d

        return _d
    if name == "SuijinAgentGraph":
        from suijin.modules.agent.lib import agent_graph

        return agent_graph.SuijinAgentGraph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _force_report(agent, thread_id, final_state, objective, config):
    return sc.force_report(agent, thread_id, final_state, objective, config)


def _print_audit_trail():
    return sc.print_audit_trail()


def _print_state_summary(agent, thread_id):
    return sc.print_state_summary(agent, thread_id)


def _build_attack_chains(trace: list) -> list:
    return sc.build_attack_chains(trace)


def _list_sessions():
    return sc.list_sessions()


def _handle_template(config):
    return sc.handle_template(config)


def _load_objective_from_file(raw_path):
    return sc.load_objective_from_file(raw_path)


def _strip_rtf(path):
    return sc._strip_rtf(path)


def run_red_team(config, objective, api_key=None, resume_state=None):
    """Sync entry point for TUI. NEVER exits silently — any crash renders
    a visible error panel (the launcher used to swallow it and drop back
    to the menu like nothing happened). `resume_state` seeds the thread
    with a saved engagement's graph state (.sje resume)."""
    from rich.panel import Panel as _Panel

    try:
        if resume_state is not None:
            asyncio.run(run_red_team_async(config, objective, api_key=api_key, resume_state=resume_state))
        else:
            asyncio.run(run_red_team_async(config, objective, api_key=api_key))
    except KeyboardInterrupt:
        console.print(
            _Panel(
                "operator interrupt — engagement ended",
                title=" OPERATOR STOP ",
                title_align="left",
                border_style="yellow",
            )
        )
    except Exception as e:  # noqa: BLE001 — the TUI must show, not swallow
        import traceback

        console.print(
            _Panel(
                f"{e}\n\n[dim]{traceback.format_exc()[-1500:]}[/dim]\nlogged: outputs/logs/engage_crash.log",
                title=" engagement crashed ",
                title_align="left",
                border_style="red",
            )
        )
        try:
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

            _d = WORKSPACE_DIR / "outputs" / "logs"
            _d.mkdir(parents=True, exist_ok=True)
            (_d / "engage_crash.log").open("a").write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {str(objective)[:80]}\n"
                + traceback.format_exc()
                + "\n"
            )
        except Exception:  # noqa: BLE001
            pass


def run_exploit(target: str = "", config=None):
    """/exploit — instant exploitation with everything already known.

    No menus, no recon phases: the objective orders the agent to mine its
    own intel first (knowledge graph, dossier, failure history) and go
    straight to the highest-probability payload. Authorization gates
    unchanged (the engagement order renders the ledger as always)."""
    if not target or not str(target).strip():
        print("usage: suijin exploit <target>")
        return 1
    target = str(target).strip()
    load_env()
    config = config or load_config()
    discover_modules()

    try:
        from suijin.modules.ops.lib.authorizations import authorization_line

        _al = authorization_line(target)
        if _al:
            console.print(f"[green]authorization on file — {_al}[/green]\n")
        else:
            console.print(
                "[yellow]no authorization on file for this target — suijin authorize <target> first[/yellow]\n"
            )
    except Exception:  # noqa: BLE001 — the gate must never block a launch
        pass

    objective = (
        f"EXPLOIT NOW — {target}. "
        "You have done enough recon. First move: call check_knowledge for this target and target_dossier — "
        "everything already known (verified vectors, blocked patterns, creds, failed attempts) is your head start. "
        "Then go STRAIGHT to exploitation: pick the highest-probability vector from the intel and fire it. "
        "No port sweeps, no directory brute-force, no further recon unless a payload needs one specific fact. "
        "write_note every attempt with its result; record_finding on anything confirmed. "
        "Show proof (response/output evidence) the moment something lands."
    )
    return run_red_team(config, objective) or 0


def main():
    load_env()
    config = load_config()
    from suijin.modules.loader import set_verbose

    set_verbose(True)  # Show module loading once at startup
    discover_modules()
    set_verbose(False)  # Silence for rest of run

    # SOUL.md — professional identity; migrate the old predator-era text
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    soul_path = WORKSPACE_DIR / "SOUL.md"
    soul_text = """# Suijin Agent — SOUL
## Professional security research, executed with discipline.
I work authorized engagements: bug-bounty programs and operator-permitted
targets, within each program's stated rules. Method over noise, evidence
over claims, reports over trophies.
"""
    try:
        if not soul_path.exists() or "predator" in soul_path.read_text(encoding="utf-8", errors="ignore").lower():
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(soul_text)
    except OSError:
        pass

    #  Objective input: type or upload
    print()
    console.print("[bold white]Load Objective:[/]")
    console.print("  [bold #ff5555]1.[/] [white]Type manually[/]")
    console.print("  [bold #58a6ff]2.[/] [white]Upload file (.txt / .md / .rtf)[/]")
    console.print("  [bold white]3.[/] [dim]Back[/]\n")

    try:
        choice = input(" ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if choice == "2":
        console.print("\n[dim]Drag file here or type path:[/]")
        try:
            raw_path = input(" ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        obj = sc.load_objective_from_file(raw_path)
        if not obj:
            return  # error already printed
    elif choice == "3":
        return
    else:
        # Default: type manually (old behavior)
        obj = input("\nTarget / Objective  ").strip()

    if obj:
        # Preview
        console.print(f"\n[dim]Objective ({len(obj)} chars):[/]")
        console.print(f"  [cyan]{obj[:500]}{'...' if len(obj) > 500 else ''}[/cyan]\n")

        # authorization ledger hook: a match renders VERIFIED into every
        # engagement order; no match gets a one-line tip
        try:
            from suijin.modules.ops.lib.authorizations import authorization_line

            _al = authorization_line(obj)
            if _al:
                console.print(f"[green]authorization on file — {_al}[/green]\n")
            else:
                console.print(
                    "[dim]tip: suijin authorize <target> --program h1 --id <auth-id> puts authorization on file[/dim]\n"
                )
        except Exception:  # noqa: BLE001 — the hook must never block a launch
            pass

        run_red_team(config, obj)


if __name__ == "__main__":
    main()
