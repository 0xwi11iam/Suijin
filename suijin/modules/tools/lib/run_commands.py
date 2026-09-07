"""Live run commands — the operator's always-on command box.

During an engagement the agent loop prints a constant stream; the operator
should still be able to steer it without waiting for a pause. RunBox starts
a daemon thread that reads stdin for the whole run: any line starting with
``/`` is dispatched instantly (``/state``, ``/report``, ``/approve 3``,
``/pause`` …), exactly like opencode's command box. Lines that don't start
with ``/`` become queued operator guidance, surfaced when the agent next
pauses.

Failure rules (non-negotiable):
- the box must NEVER break the run — every dispatch is fully guarded
- stdin may be a pipe (CI, tests) — the thread exits silently when it EOFs
- stop() is idempotent and thread-safe

Standalone by design: no imports from redteamer/blueteamer (modular-ready).
"""

from __future__ import annotations

import threading

from rich.console import Console

console = Console()


class RunBox:
    """Always-on slash-command listener for a live run."""

    def __init__(self, get_state=None, thread_id=None, config=None, console: Console | None = None):
        self._out = console or Console()
        self._get_state = get_state  # () -> dict, may return {}
        self._thread_id = thread_id
        self._config = config or {}
        self._handlers: dict[str, callable] = {}
        self._guidance: list[str] = []  # queued plain-text operator input
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._ask_mode = False  # a pending ask_operator: plain lines are answers
        for name, fn in _default_handlers(self).items():
            self.register(name, fn)

    # ── ask mode ────────────────────────────────────────────────────

    def ask_mode(self, on: bool) -> None:
        """While an ask_operator waits, plain lines are consumed as the
        answer — suppress the guidance echo."""
        with self._lock:
            self._ask_mode = bool(on)

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> "RunBox":
        if self._reader is not None or self._stop.is_set():
            return self
        self._reader = threading.Thread(target=self._read_loop, name="suijin-runbox", daemon=True)
        self._reader.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    @property
    def alive(self) -> bool:
        return bool(self._reader and self._reader.is_alive() and not self._stop.is_set())

    # ── registration ────────────────────────────────────────────────

    def register(self, name: str, fn) -> None:
        """fn(args: str) -> None. Names are stored without the slash."""
        self._handlers[name.lstrip("/").lower()] = fn

    def commands(self) -> list[str]:
        return sorted(self._handlers)

    # ── operator guidance queue ─────────────────────────────────────

    def take_guidance(self) -> list[str]:
        with self._lock:
            out, self._guidance = self._guidance, []
        return out

    # ── dispatch ────────────────────────────────────────────────────

    def dispatch(self, line: str) -> None:
        """Execute one input line. Fully guarded — never raises."""
        line = (line or "").strip()
        if not line:
            return
        if not line.startswith("/"):
            with self._lock:
                self._guidance.append(line)
                pending = len(self._guidance)
            # ask mode: a pending ask_operator consumes the line as the
            # ANSWER — no guidance echo (it read like the answer was lost)
            if not self._ask_mode:
                self._out.print(
                    f"[dim]  ▸ queued as guidance ({pending} pending) "
                    "— it reaches the agent at the next pause, or use a /command now[/dim]"
                )
            return
        cmd, _, rest = line[1:].partition(" ")
        handler = self._handlers.get(cmd.lower())
        if handler is None:
            self._out.print(f"[yellow]  ▸ unknown /{cmd} — /help lists commands[/yellow]")
            return
        try:
            handler(rest.strip())
        except Exception as e:  # a broken command must never break the run
            self._out.print(f"[red]  ▸ /{cmd} failed: {e}[/red]")

    # ── reader thread ───────────────────────────────────────────────

    def _read_loop(self) -> None:
        import sys

        try:
            for line in sys.stdin:
                if self._stop.is_set():
                    break
                self.dispatch(line)
        except Exception:
            # stdin closed / not readable (piped CI, no TTY) — box goes quiet,
            # the run continues unaffected.
            return


# ── default command set ────────────────────────────────────────────


def _default_handlers(box: RunBox) -> dict:
    def help_(_args):
        box._out.print(
            "[bold]  run commands[/bold] [dim](live — agent keeps working)[/dim]\n"
            "    /state       agent state (phase, iterations)\n"
            "    /audit       audit trail summary\n"
            "    /note <text> write an engagement note now\n"
            "    /kb <query>  quick knowledge-base search\n"
            "    /report      force report generation\n"
            "    /sessions    saved sessions\n"
            "    /cost        token + cost tally\n"
            "    /approvals   HITL queue  ->  /approve <id> | /deny <id>\n"
            "    /scope       current target scopes\n"
            "    /pause       pause agent into guidance mode\n"
            "    /panic       kill everything now (suijin panic)\n"
            "    plain text   queued as guidance for the next pause"
        )

    def state(_args):
        st = (box._get_state or (lambda: {}))() or {}
        if not st:
            box._out.print("[dim]  ▸ no state yet[/dim]")
            return
        box._out.print(
            f"  ▸ phase={st.get('current_phase', '?')} "
            f"iters={st.get('current_iteration', '?')} "
            f"msgs={len(st.get('messages', []))}"
        )

    def audit(_args):
        from suijin.modules.tools.lib.services import get as _service

        _service("red_audit_printer")()

    def note(args):
        from suijin.modules.tools.lib.intel import write_note

        if not args:
            box._out.print("[yellow]  ▸ /note <text> — text required[/yellow]")
            return
        out = write_note(args, success=True, category="operator")
        box._out.print(f"[dim]  ▸ {out}[/dim]" if isinstance(out, str) else "[dim]  ▸ noted[/dim]")

    def kb(args):
        from rich.markup import escape

        from suijin.modules.tools.lib.intel import search_kb

        if not args:
            box._out.print("[yellow]  ▸ /kb <query> — query required[/yellow]")
            return
        res = search_kb(args, limit=3)
        first = escape("\n".join(res.splitlines()[:6]))
        box._out.print(f"[dim]{first}[/dim]")

    def report(_args):
        from suijin.modules.tools.lib.services import get as _service

        st = (box._get_state or (lambda: {}))() or {}
        _service("red_force_report")(None, box._thread_id, st, "live /report", box._config)
        box._out.print("[dim]  ▸ report saved (run continues)[/dim]")

    def sessions(_args):
        from suijin.modules.tools.lib.services import get as _service

        _service("red_list_sessions")()

    def cost(_args):
        from suijin.modules.providers.lib import get_usage

        u = get_usage()
        box._out.print(
            f"  ▸ calls={u['calls']} in={u['input_tokens']:,} out={u['output_tokens']:,} "
            f"≈${u['est_cost_usd']:.4f}" + ("" if u["priced"] else " [yellow](approximate)[/yellow]")
        )

    def approvals(_args):
        from suijin.modules.ops.lib.approvals import render_list

        box._out.print(render_list())

    def approve(args):
        from suijin.modules.ops.lib.approvals import decide

        box._out.print(
            decide(int(args or 0), approve=True)
            if args.isdigit()
            else "[yellow]  ▸ /approve <id> — numeric id required[/yellow]"
        )

    def deny(args):
        from suijin.modules.ops.lib.approvals import decide

        box._out.print(
            decide(int(args or 0), approve=False)
            if args.isdigit()
            else "[yellow]  ▸ /deny <id> — numeric id required[/yellow]"
        )

    def scope(_args):
        from suijin.modules.ops.lib.governance import load_policy

        pol = load_policy()
        scopes = pol.get("allowed_target_scopes") or []
        if scopes:
            box._out.print("  ▸ " + ", ".join(scopes))
        else:
            box._out.print("  ▸ no policy file — every target allowed (see: suijin policy)")

    def pause(_args):
        import signal as _signal

        _signal._suijin_interrupted = True  # same flag the SIGINT handler sets
        box._out.print("[yellow]  ▸ pausing after the current step — guidance prompt next[/yellow]")

    def panic(_args):
        from suijin.modules.ops.lib.panic import panic

        box._out.print(panic())

    def findings(_args):
        """Triage-grouped, severity-colored — the CyberStrike-parity panel."""
        from suijin.modules.tools.lib.exploit_catalog import _expits_dir, _load_index

        root = _expits_dir()
        if not root.is_dir():
            box._out.print("[dim]  ▸ no catalog yet — findings appear here as they're proven[/dim]")
            return
        groups: dict[str, list] = {}
        for eng_dir in sorted(root.iterdir(), reverse=True):
            if not eng_dir.is_dir():
                continue
            for e in (_load_index(eng_dir).get("entries") or {}).values():
                if isinstance(e, dict):
                    groups.setdefault(str(e.get("status", "?")).upper(), []).append((eng_dir.name, e))
        if not groups:
            box._out.print("[dim]  ▸ catalog is empty[/dim]")
            return
        order = ["CONFIRMED", "AI_CLAIMED", "DRAFT", "FAILED_REPRO", "FAILED_SYNTAX", "ABANDONED"]
        sev_color = {"CRITICAL": "bold red", "HIGH": "red", "MEDIUM": "bold yellow", "LOW": "yellow"}
        for status in order + sorted(set(groups) - set(order)):
            rows = groups.get(status, [])
            if not rows:
                continue
            head_color = {"CONFIRMED": "bold green", "AI_CLAIMED": "bold yellow"}.get(status, "dim")
            box._out.print(f"  [{head_color}]{status} ({len(rows)})[/{head_color}]")
            for eng, e in rows[:12]:
                sev = str(e.get("system_severity") or e.get("severity") or "").upper()
                c = sev_color.get(sev, "white")
                box._out.print(
                    f"    [{c}]{sev or '—':8}[/{c}] {e.get('id', '?')} {str(e.get('title', ''))[:60]}"
                    f"  [dim]{eng} · {e.get('class', '?')}[/dim]"
                )

    def h1(args):
        """`/h1 EXP-001` — copy a HackerOne-formatted report for the finding
        to the clipboard (or print it when no clipboard exists)."""
        from suijin.modules.tools.lib.exploit_catalog import _expits_dir, _load_index

        eid = args.strip().upper()
        if not eid:
            box._out.print("[yellow]  ▸ /h1 <EXP-id> — e.g. /h1 EXP-001 (see /findings)[/yellow]")
            return
        entry = None
        root = _expits_dir()
        for eng_dir in sorted(root.iterdir(), reverse=True) if root.is_dir() else []:
            for k, e in (_load_index(eng_dir).get("entries") or {}).items():
                if k == eid and isinstance(e, dict):
                    entry = (eng_dir, e)
                    break
            if entry:
                break
        if not entry:
            box._out.print(f"[yellow]  ▸ {eid} not found in any catalog[/yellow]")
            return
        eng_dir, e = entry
        sev = str(e.get("system_severity") or e.get("severity") or "medium").upper()
        poc_lines = "\n".join(f"{i}. `{s.get('cmd', '')}`" for i, s in enumerate(e.get("poc", []), 1))
        text = (
            f"## {e.get('title', 'Security issue')}\n\n"
            f"**Severity**: {sev}\n**Weakness class**: {e.get('class', 'unknown')}\n"
            f"**Target**: {e.get('target', '')}\n\n"
            f"### Summary\n{e.get('description', '') or '(see dossier)'}\n\n"
            f"### Steps to reproduce\n{poc_lines or '(see exploit.yaml)'}\n\n"
            f"**Expected result**: `{e.get('marker', '')}`\n\n"
            f"### Impact\n{e.get('description', '') or 'See summary.'}\n\n"
            f"### Remediation\nFix the underlying input handling per the weakness class above; "
            f"see the engagement dossier ({eid}/finding.md) for details."
        )
        copied = False
        import shutil
        import subprocess

        for cmd, argv in (
            ("pbcopy", ["pbcopy"]),
            ("wl-copy", ["wl-copy"]),
            ("xclip", ["xclip", "-selection", "clipboard"]),
        ):
            if shutil.which(cmd):
                try:
                    subprocess.run(argv, input=text.encode(), check=True, timeout=5)
                    copied = True
                    break
                except Exception:  # noqa: BLE001
                    pass
        if copied:
            box._out.print(f"[green]  ▸ {eid} as a HackerOne report → clipboard[/green]")
        else:
            box._out.print(f"[yellow]  ▸ no clipboard — printing (pipe it yourself):[/yellow]\n{text}")

    def out(args):
        """`/out` — list offloaded tool outputs; `/out <name-part>` — print one."""
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        od = WORKSPACE_DIR / "outputs"
        files = sorted(od.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if od.is_dir() else []
        if not files:
            box._out.print("[dim]  ▸ no offloaded outputs[/dim]")
            return
        if not args:
            box._out.print("  ▸ recent offloaded outputs (full text on disk):")
            for f in files[:10]:
                box._out.print(f"    {f.name}  [dim]{f.stat().st_size:,} bytes[/dim]")
            box._out.print("[dim]    /out <name-part> prints one[/dim]")
            return
        part = args.strip().lower()
        match = next((f for f in files if part in f.name.lower()), None)
        if not match:
            box._out.print(f"[yellow]  ▸ no offloaded output matches {part!r}[/yellow]")
            return
        body = match.read_text(errors="ignore")
        box._out.print(f"[dim]  ▸ {match.name} ({len(body):,} chars) — head/tail:[/dim]")
        box._out.print(body[:2500] + ("\n…\n" + body[-800:] if len(body) > 3500 else ""))

    return {
        "help": help_,
        "state": state,
        "audit": audit,
        "note": note,
        "kb": kb,
        "report": report,
        "sessions": sessions,
        "cost": cost,
        "approvals": approvals,
        "approve": approve,
        "deny": deny,
        "scope": scope,
        "pause": pause,
        "panic": panic,
        "findings": findings,
        "h1": h1,
        "out": out,
    }


HINT = "[dim]  ▸ live commands: type /help anytime (agent keeps running) — /state /note /kb /pause /panic[/dim]"
