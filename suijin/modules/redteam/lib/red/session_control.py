"""
suijin/core/red/session_control.py — Runtime commands & helpers for Red Team.

Extracted from redteamer.py. Handles report generation, audit printing,
state summary, attack chain building, session listing, template browsing,
and objective file loading.
"""

from __future__ import annotations

import os

from rich.console import Console

console = Console()


def force_report(agent, thread_id, final_state, objective, config):
    """Generate a full engagement report on demand."""
    from suijin.modules.loader import load_local_module
    from suijin.modules.tools.lib.audit_trail import get_audit_json
    from suijin.modules.tools.lib.report_exporter import generate_report

    providers = load_local_module("providers")
    state = final_state or agent.get_state(thread_id) or {}
    trace = state.get("execution_trace", [])
    findings = get_audit_json().get("findings", [])
    path = generate_report(
        engagement_name=objective[:80],
        execution_trace=trace,
        findings=findings,
        target_info=state.get("target_info", {}),
        messages=state.get("messages", []),
        cost_usd=providers.USAGE.get("est_cost_usd", 0),
        completion_reason=state.get("completion_reason", ""),
        attack_chains=build_attack_chains(trace),
    )
    console.print(f"[green]Report saved: {path}[/green]")
    # Also end audit trail
    from suijin.modules.loader import load_local_module as _llm
    from suijin.modules.tools.lib.audit_trail import end_audit

    end_audit(_llm("providers").USAGE.get("est_cost_usd", 0))


def print_audit_trail():
    """Print a summary of the current audit trail."""
    from suijin.modules.tools.lib.audit_trail import get_audit_json

    trail = get_audit_json()
    if not trail or not trail.get("iterations"):
        console.print("[dim]No audit trail data yet.[/dim]")
        return
    console.print(
        f"[bold]Audit Trail: {len(trail['iterations'])} iterations, {len(trail.get('findings', []))} findings[/bold]"
    )


def print_state_summary(agent, thread_id):
    """Print current agent state summary."""
    state = agent.get_state(thread_id)
    if not state:
        console.print("[dim]No state available.[/dim]")
        return
    phase = state.get("current_phase", "?")
    iters = state.get("current_iteration", 0)
    msgs = len(state.get("messages", []))
    console.print(f"[bold]State:[/bold] phase={phase}, iters={iters}, msgs={msgs}")


def build_attack_chains(trace: list) -> list:
    """Build attack chains from execution trace for Mermaid diagrams."""
    chains = []
    current_chain = {"steps": []}
    for step in trace:
        tn = step.get("tool_name", "?")
        success = step.get("success", True)
        label = f"{tn} ({'OK' if success else 'FAIL'})"
        current_chain["steps"].append(label)
        if step.get("completion_reason"):
            chains.append(current_chain)
            current_chain = {"steps": []}
    if current_chain["steps"]:
        chains.append(current_chain)
    return chains


def list_sessions():
    """List saved sessions for replay."""
    from suijin.modules.tools.lib.session_replay import list_sessions as _list

    sessions = _list()
    if not sessions:
        console.print("[dim]No saved sessions.[/dim]")
        return
    console.print("[bold]Saved Sessions:[/bold]")
    for i, s in enumerate(sessions[:10], 1):
        summary = s.get("state_summary", {})
        console.print(f"  {i}. [{s.get('saved_at', '?')}] {s.get('objective', '?')[:60]}")
        console.print(
            f"     phase={summary.get('phase', '?')}, iters={summary.get('iterations', 0)}, cost=${s.get('cost_usd', 0):.4f}"
        )


def handle_template(config):
    """Interactive template browser — view, load, or create engagement templates."""
    from suijin.modules.platform.lib.templates import list_templates, load_template, save_template

    templates = list_templates()
    console.print(f"\n[bold]Available Templates ({len(templates)}):[/bold]")
    for i, t in enumerate(templates, 1):
        tmpl = load_template(t)
        console.print(f"  [bold]{i}.[/bold] [cyan]{t}[/cyan] — {tmpl.get('description', '')[:80]}")
    console.print(f"  [bold]{len(templates) + 1}.[/bold] [yellow]New template (AI designs one)[/yellow]")
    console.print(f"  [bold]{len(templates) + 2}.[/bold] [dim]Cancel[/dim]")
    try:
        choice = console.input("\n[bold cyan]  Select template  [/bold cyan]").strip()
        idx = int(choice) - 1
        if 0 <= idx < len(templates):
            name = templates[idx]
            tmpl = load_template(name)
            config["template"] = name
            config.update(
                {k: tmpl[k] for k in ["ports", "wordlists", "checks", "max_iterations", "headless"] if k in tmpl}
            )
            console.print(f"[green]  Loaded template: {name}[/green]")
            console.print(
                f"  Ports: {tmpl.get('ports', [])} | Checks: {tmpl.get('checks', [])} | Max iters: {tmpl.get('max_iterations', '?')}"
            )
        elif idx == len(templates):
            # AI designs a new template
            console.print(
                "[dim]  Describe the template you want (e.g. 'quick WordPress scan with SQLi and XSS'):[/dim]"
            )
            desc = console.input("[bold cyan]  Description  [/bold cyan]").strip()
            if desc:
                name = desc.replace(" ", "_").lower()[:30]
                new_tmpl = {
                    "name": desc[:60],
                    "description": desc,
                    "ports": [80, 443],
                    "wordlists": ["common.txt"],
                    "tools": ["nmap", "whatweb", "gobuster"],
                    "checks": ["sqli", "xss"],
                    "max_iterations": 25,
                    "headless": False,
                }
                # AI-enhanced: if keywords match, expand config
                desc_lower = desc.lower()
                if "api" in desc_lower:
                    new_tmpl["ports"].extend([3000, 5000, 8000])
                    new_tmpl["checks"].extend(["jwt", "cors", "mass_assignment"])
                if "spa" in desc_lower or "react" in desc_lower or "javascript" in desc_lower:
                    new_tmpl["headless"] = True
                if "wordpress" in desc_lower:
                    new_tmpl["ports"].append(8080)
                    new_tmpl["checks"].extend(["sqli", "file_upload"])
                if "cloud" in desc_lower:
                    new_tmpl["checks"].extend(["ssrf", "information_disclosure", "subdomain_takeover"])
                if "full" in desc_lower or "everything" in desc_lower:
                    new_tmpl = load_template("full_assault")
                path = save_template(name, new_tmpl)
                config["template"] = name
                config.update(
                    {
                        k: new_tmpl[k]
                        for k in ["ports", "wordlists", "checks", "max_iterations", "headless"]
                        if k in new_tmpl
                    }
                )
                console.print(f"[green]  Template saved: {path}[/green]")
        else:
            console.print("[dim]  Cancelled.[/dim]")
    except (ValueError, KeyboardInterrupt, EOFError):
        console.print("[dim]  Cancelled.[/dim]")


def load_objective_from_file(raw_path: str) -> str | None:
    """Load objective text from a file. Handles drag-drop paths with quotes/spaces.

    Supports: .txt, .md, .rtf (rich text stripped to plain text).
    Returns the objective string or None on failure.
    """
    # Clean drag-drop artifacts: quotes, escaped spaces, trailing whitespace
    path = raw_path.strip()
    # Remove surrounding quotes (single or double)
    if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
        path = path[1:-1]
    # Handle escaped spaces from drag-drop
    path = path.replace("\\ ", " ")

    # Resolve ~ and relative paths
    path = os.path.expanduser(path)
    path = os.path.abspath(path)

    if not os.path.exists(path):
        console.print(f"[bold red]File not found:[/] {path}")
        return None
    if not os.path.isfile(path):
        console.print(f"[bold red]Not a file:[/] {path}")
        return None

    ext = os.path.splitext(path)[1].lower()
    console.print(f"[dim]Loading: {path} ({ext})[/]")

    try:
        if ext == ".rtf":
            text = _strip_rtf(path)
        else:
            # .txt, .md, or anything else — read as plain text
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
    except Exception as e:
        console.print(f"[bold red]Read error:[/] {e}")
        return None

    text = text.strip()
    if not text:
        console.print("[bold red]File is empty.[/]")
        return None

    console.print(f"[dim]Extracted {len(text)} characters.[/]")
    return text


def _strip_rtf(path: str) -> str:
    """Extract plain text from an RTF file. Falls back to raw if no rtf parser."""
    try:
        from striprtf.striprtf import rtf_to_text

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return rtf_to_text(f.read())
    except ImportError:
        pass
    # Fallback: strip RTF tags with regex
    import re

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    # Remove RTF control words and groups
    content = re.sub(r"\\[a-z]+\d*", "", content)  # control words
    content = re.sub(r"\\[{}]", "", content)  # escaped braces
    content = re.sub(r"[{}]", "", content)  # group braces
    content = re.sub(r"\\\n", "\n", content)  # line continuations
    return content.strip()


# ── Pause console: 15 course-changing commands + free-form guidance ──────

PHASES = ("informational", "exploitation", "post_exploitation")


class PauseContext:
    """Everything the pause commands need, injected by the engagement loop.
    Kept as a plain namespace so tests can fake every knob."""

    def __init__(
        self,
        *,
        console,
        agent=None,
        langgraph_config=None,
        thread_id="",
        config=None,
        objective="",
        route_tool_fn=None,
        usage_fn=None,
        loot=None,
        force_report_fn=None,
    ):
        self.console = console
        self.agent = agent
        self.langgraph_config = langgraph_config
        self.thread_id = thread_id
        self.config = config or {}
        self.objective = objective
        self.route_tool_fn = route_tool_fn or (lambda name, args: f"(no route: {name})")
        self.usage_fn = usage_fn or (lambda: {})
        self.loot = loot or {"flags": [], "creds": []}
        self.force_report_fn = force_report_fn
        self.guidance_extra: list[str] = []  # /focus /skip /finish accumulate here


def build_pause_handlers(ctx: PauseContext) -> dict:
    """/command -> handler(args). 15 commands, each fully testable."""

    def _report(_args):
        ctx.console.print("[dim]  Generating report...[/dim]")
        if ctx.force_report_fn:
            ctx.force_report_fn()
        ctx.console.print("[dim]  Report saved.[/dim]")

    def _audit(_args):
        print_audit_trail()

    def _state(_args):
        if ctx.agent is not None:
            print_state_summary(ctx.agent, ctx.thread_id)

    def _sessions(_args):
        list_sessions()

    def _template(_args):
        handle_template(ctx.config)

    def _health(_args):
        from suijin.modules.platform.lib.templates import print_health_check

        print_health_check(ctx.console)

    def _objective(args):
        if not args:
            ctx.console.print("[yellow]  usage: /objective <new objective text>[/yellow]")
            return
        if ctx.agent is not None:
            try:
                ctx.agent._graph.update_state(
                    ctx.langgraph_config,
                    {
                        "original_objective": args,
                        "_objective": args,
                        "messages": [{"role": "user", "content": f"OPERATOR: objective changed to: {args}"}],
                    },
                )
            except Exception as e:  # noqa: BLE001 — pause must never crash
                ctx.console.print(f"[yellow]  objective change failed: {e}[/yellow]")
                return
        ctx.objective = args
        ctx.console.print(f"[green]  objective -> {args[:120]}[/green]")

    def _phase(args):
        p = args.strip().lower().replace("-", "_")
        if p not in PHASES:
            ctx.console.print(f"[yellow]  usage: /phase <{'|'.join(PHASES)}>[/yellow]")
            return
        if ctx.agent is not None:
            try:
                ctx.agent._graph.update_state(
                    ctx.langgraph_config,
                    {
                        "current_phase": p,
                        "messages": [{"role": "user", "content": f"PHASE TRANSITION: operator forced {p} phase."}],
                    },
                )
            except Exception as e:  # noqa: BLE001
                ctx.console.print(f"[yellow]  phase change failed: {e}[/yellow]")
                return
        ctx.console.print(f"[green]  phase -> {p}[/green]")

    def _focus(args):
        if not args:
            ctx.console.print("[yellow]  usage: /focus <what to focus on next>[/yellow]")
            return
        ctx.guidance_extra.append(f"Focus on: {args}")
        ctx.console.print("[dim]  focus noted — it rides the next guidance.[/dim]")

    def _skip(_args):
        ctx.guidance_extra.append(
            "Abandon the current approach entirely — it is a dead end. Pivot to a different attack vector now."
        )
        ctx.console.print("[dim]  skip noted — pivot instruction queued.[/dim]")

    def _finish(_args):
        ctx.guidance_extra.append(
            "Wrap up NOW: run generate_report on what you have, then emit action=complete. No new exploration."
        )
        ctx.console.print("[dim]  finish noted — wrap-up instruction queued.[/dim]")

    def _loot(_args):
        if ctx.loot.get("flags"):
            for f in ctx.loot["flags"]:
                ctx.console.print(f"  [#e6b47c]FLAG[/#e6b47c]  {f}")
        else:
            ctx.console.print("  [dim]no flags captured yet[/dim]")
        for kind, v in ctx.loot.get("creds", []):
            ctx.console.print(f"  [green]CRED[/green]  {kind}: {str(v)[:70]}")

    def _jobs(_args):
        ctx.console.print(str(ctx.route_tool_fn("job_list", {})))

    def _kill(args):
        jid = args.strip()
        if not jid:
            ctx.console.print("[yellow]  usage: /kill <job_id>[/yellow]")
            return
        ctx.console.print(str(ctx.route_tool_fn("job_cancel", {"job_id": jid})))

    def _cost(_args):
        from suijin.modules.redteam.lib.red.console_ui import UI_STATE, _fmt_tok

        u = ctx.usage_fn() or {}
        tok = int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0))
        ttft = UI_STATE.get("last_ttft")
        ttft_s = f" | first token {ttft}s" if ttft is not None else ""
        ctx.console.print(
            f"  calls {u.get('calls', 0)} | {_fmt_tok(tok)} tok "
            f"(api {u.get('api_reported_calls', 0)} / est {u.get('estimated_calls', 0)}) "
            f"| ${float(u.get('est_cost_usd', 0)):.4f}{ttft_s}"
        )

    return {
        "/report": _report,
        "/audit": _audit,
        "/state": _state,
        "/sessions": _sessions,
        "/template": _template,
        "/health": _health,
        "/objective": _objective,
        "/phase": _phase,
        "/focus": _focus,
        "/skip": _skip,
        "/finish": _finish,
        "/loot": _loot,
        "/jobs": _jobs,
        "/kill": _kill,
        "/cost": _cost,
    }


PAUSE_BANNER = (
    "\n[bold yellow]  Paused[/bold yellow] [dim]— /objective /phase /focus /skip /finish /loot /jobs "
    "/kill /cost /report /audit /state /sessions /template /health — or type guidance (Ctrl+C to quit)[/dim]"
)


def pause_console(ctx: PauseContext, input_fn) -> str:
    """Run the pause loop: slash commands dispatch (stay in the loop),
    the first non-slash line is the guidance. Returns the guidance string
    (queued /focus /skip /finish instructions merged in)."""
    handlers = build_pause_handlers(ctx)
    ctx.console.print(PAUSE_BANNER)
    while True:
        line = (input_fn("  Guidance  ") or "").strip()
        cmd, _, cmd_args = line.partition(" ")
        handler = handlers.get(cmd.lower())
        if handler is None:
            guidance = line
            break
        handler(cmd_args.strip())
    extras = [g for g in ctx.guidance_extra if g]
    if extras:
        guidance = " ".join(extras + ([guidance] if guidance else []))
    return guidance or "Continue what you were doing."
