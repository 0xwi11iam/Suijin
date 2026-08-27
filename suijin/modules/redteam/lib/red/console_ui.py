"""Engagement console UI — the red-teamer's live view.

Rich only, no emojis. One iteration = ONE box, split by separator lines:

    ╭─ #3 · informational · +2.1k tok · +$0.0045 ─────────────╮
    │ thinking  While nmap runs, fingerprint the stack        │   dim blue
    │ ─────────────────────────────────────────────────────── │
    │ said: DNS may fail; verify resolution first             │   bright cyan
    │ ─────────────────────────────────────────────────────── │
    │ ❯ execute_terminal                                       │
    │ curl -s https://…                                        │   syntax highlight
    │ ─────────────────────────────────────────────────────── │
    │ Status: 200 …                                            │   syntax highlight
    ╰──────────────────────────────────────────────────────────╯

- The box STREAMS: while the tool runs, the live region already shows the
  box with thinking + said + command; output lands when the tool answers,
  then the completed box freezes above the strip.
- No truncation anywhere. The box flexes to its content.
- The pinned strip (spinner while the LLM thinks) stays below the live box.
- Errors render gracefully: one meaningful red line, never tracebck walls.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

GOLD = "#e6b47c"  # red-team accent (repo convention)
BORDER = "#30363d"
RED = "#ff5555"
GREEN = "#3fb950"

FLAG_RE = re.compile(r"FLAG\{[^}\s]{1,128}\}")
CRED_RES = [
    ("AWS key", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    ("AWS secret", re.compile(r"\b(AWS_SECRET_ACCESS_KEY[=:]\s*\S{8,})")),
    ("OpenAI-style key", re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("Slack token", re.compile(r"\b(xox[abprs]-[A-Za-z0-9-]{10,})\b")),
    ("Google API key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b")),
    ("JWT", re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")),
    ("Bearer token", re.compile(r"\b(bearer\s+[A-Za-z0-9._-]{16,})", re.I)),
]

# module-level so RunBox's /think can flip it from the reader thread
UI_STATE = {
    "show_reasoning": False,  # opencode-style: reasoning HIDDEN until /think
    "last_ttft": None,  # seconds to the first streamed token (the proof streaming works)
    "input_mode": "recon",  # the input box mode badge (Tab cycles)
    "input_buf": None,  # None = idle hint; str = live typing (cursor ▌)
    "flags": [],
    "creds": [],
    "fireteams": 0,
    "last_reasoning": "",
    "last_result_success": True,
}


def _fireteam_snapshot() -> list:
    """Live fireteam registry (same process as the agent). Guarded — a UI
    render must never crash on agent internals."""
    try:
        from suijin.modules.agent.lib.nodes.subagent_node import _snapshot

        return _snapshot().get("teams", [])
    except Exception:
        return []


def _fireteam_live_count() -> int:
    """Specialists actually running RIGHT NOW (registry truth — the old
    strip counter only ever went up)."""
    return sum(int(t.get("running", 0)) for t in _fireteam_snapshot())


def _fireteam_total() -> int:
    """All agents across live teams (running + finished-but-undrained);
    a snapshot without task detail still counts its running specialists."""
    total = 0
    for t in _fireteam_snapshot():
        running = int(t.get("running", 0))
        if running > 0:
            total += max(len(t.get("tasks", [])), running)
    return total


def _fireteam_agent_rows() -> list:
    """Per-agent live lines in the bottom bar: `agent N: <task> ⠋` while
    running, ✓/✗ for finished siblings. The WHOLE block appears only while
    a team is actually running and disappears the moment nothing is.

    Smoothness: each running agent gets its OWN Spinner OBJECT (not a
    pre-rendered frame) — Rich's Live auto-refresh re-renders renderables
    at refresh_per_second, so the animation is native 60fps; the 1s
    heartbeat only refreshes the counts."""
    rows: list = []
    for team in _fireteam_snapshot():
        running = int(team.get("running", 0))
        if not running:
            continue  # dead/undrained team — the bar shows nothing (operator contract)
        done = sum(1 for t in team.get("tasks", []) if t.get("state") == "done")
        rows.append(
            Text.assemble(
                ("Fireteam ", "bold magenta"),
                (str(team.get("team_id", "?")), "magenta"),
                (f" · {running} running · {done} done", "dim"),
            )
        )
        for i, t in enumerate(team.get("tasks", []), 1):
            task = str(t.get("task", ""))[:52]
            state = t.get("state")
            if state == "running":
                # a grid cell holds the LIVE spinner object → native animation
                g = Table.grid(padding=(0, 0))
                g.add_row(Text(f"  agent {i}: {task} ", style="dim"), Spinner("dots", style="magenta", speed=3.0))
                rows.append(g)
            elif state == "done":
                ok = bool(t.get("success"))
                mark = "✓" if ok else "✗"
                style = "green" if ok else "red"
                rows.append(Text.assemble((f"  agent {i}: ", "dim"), (task, "dim"), (f" {mark}", style)))
            # queued tasks stay silent — no noise
    return rows[:8]  # strip height guard (header + agents, few teams)


def ask_operator_answer(
    run_box, console: Console, question: str, timeout_s: float = 600.0, label: str = "Answer"
) -> str:
    """Get the operator's answer through the RunBox reader — the ONE thread
    that owns stdin (a main-thread input() raced the reader and hung the
    agent). Prompt is exactly `Answer:` on its own line; the first plain
    line typed is the answer (slash commands still dispatch normally)."""
    import time as _time

    console.print(f"[bold cyan]{label}:[/bold cyan] ", end="")
    console.file.flush()
    run_box.ask_mode(True)  # plain lines are the answer — no guidance echo
    deadline = _time.monotonic() + timeout_s
    run_box.take_guidance()  # drain stale lines queued before the question
    while _time.monotonic() < deadline:
        lines = run_box.take_guidance()
        if lines:
            run_box.ask_mode(False)
            return lines[0].strip()
        _time.sleep(0.15)
    run_box.ask_mode(False)
    return ""


def toggle_reasoning(console: Console | None = None) -> bool:
    """Flip the 'said' section visibility. Returns the new state."""
    UI_STATE["show_reasoning"] = not UI_STATE["show_reasoning"]
    if console is not None:
        state = "shown" if UI_STATE["show_reasoning"] else "hidden"
        console.print(f"[dim]  said {state}[/dim]")
        if UI_STATE["show_reasoning"] and UI_STATE["last_reasoning"]:
            console.print(_md(UI_STATE["last_reasoning"], "cyan"))
    return UI_STATE["show_reasoning"]


def loot_in(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Extract (flags, [(kind, value)]) from a tool output string."""
    flags = []
    for f in FLAG_RE.findall(text or ""):
        if f not in flags:
            flags.append(f)
    creds = []
    for kind, rx in CRED_RES:
        for m in rx.findall(text or ""):
            v = m.strip()
            if (kind, v) not in creds:
                creds.append((kind, v))
    return flags, creds


# ── graceful errors ─────────────────────────────────────────────────────


def graceful_error(text: str) -> str:
    """Collapse a raw transport/tool exception blob into the human line."""
    t = (text or "").strip()
    t = re.sub(r"Traceback \(most recent call last\):.*?(?=\w+Error|\w+Exception)", "", t, flags=re.S)
    t = " ".join(t.split())
    m = re.search(r"Failed to resolve ['\"]?([^'\"()\s]+)", t)
    if m:
        return f"could not resolve {m.group(1)} (DNS)"
    m = re.search(r"NameResolutionError\(['\"]?Failed to resolve (\S+)", t)
    if m:
        return f"could not resolve {m.group(1)} (DNS)"
    m = re.search(r"Connection to ([\d.]+)(?: port (\d+))? (failed|refused|timed out)", t)
    if m:
        port = f":{m.group(2)}" if m.group(2) else ""
        return f"connection to {m.group(1)}{port} {m.group(3)}"
    m = re.search(r"Max retries exceeded with url (\S+)", t)
    if m:
        return f"unreachable: {m.group(1)[:80]}"
    if re.search(r"ReadTimeoutError.*?timeout", t) or re.search(r"timed out", t):
        return "request timed out"
    m = re.search(r"(?:sh: )?(\S+): command not found", t)
    if m:
        return f"{m.group(1)} not installed"
    m = re.search(r"(\w+(?:Error|Exception))[:\s](.{10,160})", t)
    if m:
        return f"{m.group(1)}: {m.group(2).strip()}"
    return t[:200]


def is_error(text: str) -> bool:
    o = (text or "").lstrip()
    return o.startswith(("Error:", "Tool error:", "Tool Error", "HTTP Error", "Execution Fault", "Traceback"))


# ── syntax rendering ────────────────────────────────────────────────────


def _bash_args(a: dict) -> str:
    return str(a.get("cmd") or a.get("command") or a.get("script") or "")


def _json_args(a: dict) -> str:
    try:
        return json.dumps(a, indent=2, default=str)
    except Exception:  # noqa: BLE001 — never break a render
        return str(a)


def _kv_args(keys: tuple[str, ...]):
    """Compact 'k=v' line for small-arg tools (search_cve, job_status, …)."""

    def render(a: dict) -> str:
        parts = [f"{k}={a[k]}" for k in keys if a.get(k) not in (None, "")]
        return " ".join(parts)

    return render


_LEXERS: dict[str, tuple[str, object]] = {
    # shell + http (existing)
    "execute_terminal": ("bash", _bash_args),
    "http_request": ("json", _json_args),
    # scanners composed through execute_terminal — direct pack calls get bash too
    "nmap": ("bash", lambda a: "nmap " + str(a.get("target", "")) + " " + str(a.get("flags", "")).strip()),
    "gobuster": ("bash", lambda a: "gobuster " + str(a.get("args", a.get("flags", "")))),
    "ffuf": ("bash", lambda a: "ffuf " + str(a.get("args", a.get("flags", "")))),
    "sqlmap": ("bash", lambda a: "sqlmap " + str(a.get("args", a.get("cmd", "")))),
    "nikto": ("bash", lambda a: "nikto " + str(a.get("args", a.get("target", "")))),
    "hydra": ("bash", lambda a: "hydra " + str(a.get("args", a.get("cmd", "")))),
    "msf_command": ("ruby", _bash_args),
    # content-bearing tools — show the CONTENT, not an args dict
    "write_file": ("python", lambda a: str(a.get("content", ""))),
    "write_note": ("markdown", lambda a: str(a.get("content", ""))),
    "edit_skill": ("python", lambda a: str(a.get("new_content", ""))),
    "write_tool": ("python", lambda a: str(a.get("code", ""))),
    "read_file": ("text", lambda a: str(a.get("file_path", ""))),
    "kb_read": ("text", lambda a: str(a.get("path", ""))),
    "fetch_authorization_page": ("text", _kv_args(("target", "url"))),
    "js_bundle_analyze": ("text", _kv_args(("url",))),
    "google_key_probe": ("text", _kv_args(("key",))),
    "source_map_probe": ("text", _kv_args(("url",))),
    # key=value one-liners
    "search_cve": ("text", _kv_args(("software", "version", "limit"))),
    "search_kb": ("text", _kv_args(("keyword", "limit"))),
    "check_knowledge": ("text", _kv_args(("target", "payload"))),
    "record_finding": ("text", _kv_args(("target", "finding_type", "rule", "evidence"))),
    "web_search": ("text", _kv_args(("query", "max_results"))),
    "job_status": ("text", _kv_args(("job_id",))),
    "job_wait": ("text", _kv_args(("job_id", "timeout"))),
    "job_output": ("text", _kv_args(("job_id",))),
    "job_cancel": ("text", _kv_args(("job_id",))),
    "claim_flag": ("text", _kv_args(("flag",))),
    "pip_install": ("bash", _kv_args(("package",))),
    "apply_patch": ("text", _kv_args(("vulnerability", "file_path"))),
    "mutate_wordlist": ("text", _kv_args(("wordlist", "mutations"))),
    "cewl_words": ("text", _kv_args(("url", "depth"))),
    "find_wordlist": ("text", _kv_args(("kind", "pattern"))),
    "wordlist_tool": ("text", _kv_args(("mode", "path", "lines"))),
    "suggest_exploit": ("text", _kv_args(("service", "version"))),
    "extract_payloads": ("text", _kv_args(("source",))),
    "mine_failures": ("text", _kv_args(("engagement",))),
    "anonymize_report": ("text", _kv_args(("file_path",))),
    "attack_tree": ("text", _kv_args(("objective",))),
    "diff_response": ("text", _kv_args(("url_a", "url_b"))),
    "normalize_output": ("text", _kv_args(("mode",))),
    "target_dossier": ("text", _kv_args(("target",))),
    "rate_limit_check": ("text", _kv_args(("endpoint",))),
    "evidence_capture": ("text", _kv_args(("label", "path"))),
    "evidence_verify": ("text", _kv_args(("bundle",))),
    "recon_chain": ("bash", _kv_args(("target", "ports"))),
    "cve_advise_tools": ("text", _kv_args(("keyword",))),
    "recipe_run": ("text", _kv_args(("name", "target"))),
    "msf_run": ("json", _json_args),
    "payload_generate": ("json", _json_args),
    "recipe_define": ("json", _json_args),
}

# tools that take NO meaningful args — render the tool line only, no block
_NO_ARGS_TOOLS = {
    "job_list",
    "list_skills",
    "list_own_files",
    "fireteam_status",
    "recipe_list",
    "kb_stats",
    "kb_freshness",
    "msf_check",
    "msf_sessions",
    "rate_limit_all",
    "generate_report",
    "deploy_subagent",  # rendered via fireteam(), never as a command block
}


def _guess_output_lexer(text: str) -> str:
    """Best-effort lexer for tool OUTPUT bodies (json/html/xml, else text)."""
    t = (text or "").lstrip()
    if t.startswith(("{", "[")):
        with contextlib.suppress(Exception):
            json.loads(text)
            return "json"
    low = t[:300].lower()
    if "<html" in low or "<!doctype html" in low:
        return "html"
    if t.startswith("<?xml"):
        return "xml"
    return "text"


def _syntax(console: Console, code: str, lexer: str) -> object:
    """Syntax block, or plain dim text off-terminal. Never raises."""
    if not console.is_terminal or lexer == "text":
        return Text(code, style="dim")
    with contextlib.suppress(Exception):
        return Syntax(code, lexer, theme="monokai", word_wrap=True, background_color="default", pad=False)
    return Text(code, style="dim")


def _fmt_tok(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _crash_log(where: str, exc: BaseException) -> None:
    """Field crashes died silently and took the diagnosis with them —
    every guarded render failure lands here: outputs/logs/ui_crash.log"""
    import traceback

    try:
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        d = WORKSPACE_DIR / "outputs" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "ui_crash.log").open("a").write(
            f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} [{where}] {exc!r}\n" + traceback.format_exc() + "\n"
        )
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def _md(text: str, style: str = "none") -> Markdown:
    """Markdown render for model-authored text (thinking/said/findings/
    plain tool output) — the model writes markdown; show it as markdown."""
    return Markdown(text or "", code_theme="monokai", style=style, hyperlinks=False)


_BLOCKED_PREFIXES = ("TOOL NOT FOUND", "policy:", "BLOCKED", "Error: tool")


def _is_blocked(out: str) -> bool:
    o = (out or "").lstrip()
    return any(o.startswith(p) for p in _BLOCKED_PREFIXES)


def _guarded(name: str, fn):
    """Wrap a UI method: on exception, log + plain-text fallback, never raise.
    `fn` is the UNBOUND class function; `self` rides in *a (the wrapper is
    instance-bound exactly once)."""

    def wrapper(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except Exception as e:  # noqa: BLE001 — the UI must never kill a run
            _crash_log(name, e)
            try:
                # VISIBLE notice, never silent: the operator sees the content
                # plus a one-line note that the renderer fell back
                txt = " ".join(str(x)[:200] for x in a if isinstance(x, str))
                if txt:
                    self.console.print(Text(txt[:400], style="dim"))
                self.console.print(Text(f"[render fallback: {name}: {type(e).__name__} — logged]", style="yellow"))
            except Exception:  # noqa: BLE001
                pass

    return wrapper


class _Iteration:
    """One streamed iteration: a titled top rule, printed sections, and a
    closing rule colored by outcome. NOT part of the live region — content
    prints above the strip as it arrives (stable, no repaint storms)."""

    def __init__(self, n: int, phase: str, dt_tok: int, dt_cost: float):
        self.n = n
        self.phase = phase
        self.dt_tok = dt_tok
        self.dt_cost = dt_cost
        self.sections = 0
        self.open = False
        self.ok = True


class EngagementUI:
    """One rule-delimited block per iteration; the live region is ONLY the
    one-row strip (spinner while the LLM thinks, stats otherwise)."""

    def __init__(self, console: Console, objective: str = ""):
        self.console = console
        self.objective = objective
        # UNCRASHABLE: a render bug must never kill an engagement (a field
        # run died mid-render back to the menu with zero output). Every
        # public method is guarded; failures log to outputs/logs/ui_crash.log
        # and fall back to plain text.
        import types

        for _name in (
            "iteration_header",
            "thinking",
            "reasoning",
            "reasoning_delta",
            "stream_done",
            "tool",
            "planned_steps",
            "parse_note",
            "output",
            "loot",
            "supervisor",
            "oracle",
            "drift",
            "fireteam",
            "phase_transition",
            "ask",
            "done",
            "failure",
            "flush_open",
        ):
            _fn = getattr(type(self), _name, None)  # UNBOUND class function
            if _fn is None:
                continue
            setattr(self, _name, types.MethodType(_guarded(_name, _fn), self))
        self._live: Live | None = None
        # Rich speed is a MULTIPLIER over the spinner's base interval
        # (dots = 80ms/frame): 3.0 → ~27ms/frame ≈ 37fps. (Earlier values
        # 0.4→0.05 read as "faster" but divided the rate — 0.05 was 20x
        # SLOWER than default; that's why the spinner looked dead.)
        self._spinner = Spinner("dots", style=GOLD, speed=3.0)
        self.iteration = 0
        self.phase = "starting"
        self._cur: _Iteration | None = None
        self._waiting = True
        self._last_tok = 0
        self._refresh_thread: threading.Thread | None = None
        self._refresh_stop = threading.Event()
        # streaming reasoning (the flexing box): deltas land here live
        self._stream_buf: list[str] = []
        self._streaming = False
        self._waiting_since: float | None = None  # think-turn start → TTFT proof
        self._last_cost = 0.0

    # ── strip (the ONLY live region — one stable row) ──────────────────

    def _strip(self):
        from suijin.modules.providers.lib import USAGE

        tok = int(USAGE.get("input_tokens", 0)) + int(USAGE.get("output_tokens", 0))
        cost = float(USAGE.get("est_cost_usd", 0.0))
        approx = "" if USAGE.get("priced", True) else "~"
        if self._waiting:
            # thinking + dots — the label the operator asked for
            g = Table.grid(padding=(0, 1))
            g.add_row(self._spinner, Text("thinking", style=f"bold {GOLD}"))
            left = g
        else:
            left = Text(f"suijin {self.phase} #{self.iteration}", style=f"bold {GOLD}")
        ft_live = _fireteam_live_count()
        ft_total = _fireteam_total()
        # the compact segment lives WHERE 'FT 1' lived — same stats row,
        # more content: running/total straight from the registry
        ft_seg = None
        if ft_live:
            label = f"Fireteam {ft_live}/{ft_total} live" if ft_total != ft_live else f"Fireteam {ft_live} live"
            ft_seg = [(" | ", "dim"), (label, "bold magenta")]
        right = Text.assemble(
            (f"{_fmt_tok(tok)} tok", "cyan"),
            (" | ", "dim"),
            (f"{approx}${cost:.4f}", "cyan"),
            *([(" | ", "dim"), (f"FLAG {len(UI_STATE['flags'])}", f"bold {GOLD}")] if UI_STATE["flags"] else []),
            *([(" | ", "dim"), (f"CRED {len(UI_STATE['creds'])}", "bold green")] if UI_STATE["creds"] else []),
            *(ft_seg or []),
        )
        t = Table.grid(expand=True, padding=(0, 1))
        t.add_row(left, Text(), right)
        t.columns[1].ratio = 1
        rows = []
        panel = self._stream_panel()
        if panel is not None:
            rows.append(panel)  # the flexing box sits directly above the stats
        rows.append(t)
        rows.extend(_fireteam_agent_rows())
        rows.append(self._input_box_row())  # the input box is ALWAYS the bottom row
        return Group(*rows)

    def _input_box_row(self):
        """The operator's prompt — a real white box, always at the bottom:
        [⠋ thinking] [MODE] » type here▌ — the spinner is a live renderable
        (native 60fps), Tab cycles the mode badge."""
        mode = str(UI_STATE.get("input_mode", "recon")).upper()
        g = Table.grid(padding=(0, 1))
        g.add_row(
            self._spinner,  # live object — animates under Live's auto-refresh
            Text("thinking" if self._waiting else "working", style=f"bold {GOLD}" if self._waiting else "dim"),
            Text(mode, style="bold black on bright_white"),
        )
        buf = UI_STATE.get("input_buf")
        if buf is not None:
            body = Table.grid(padding=(0, 0))
            body.add_row(g, Text.assemble((" » ", f"bold {GOLD}"), (str(buf)[:60], "bold white"), ("▌", GOLD)))
        else:
            hint = f" » Tab:mode  ESC ESC:pause  / for commands — {mode.lower()} prompt"
            body = Table.grid(padding=(0, 0))
            body.add_row(g, Text(hint, style="dim"))
        return Panel(
            body,
            box=box.SQUARE,
            border_style="bright_white",
            padding=(0, 1),
            expand=True,
        )

    def set_input(self, buf) -> None:
        """Live typing into the box (None = idle hint)."""
        UI_STATE["input_buf"] = None if buf is None else str(buf)
        self._tick()

    def set_mode(self, mode: str) -> None:
        """The mode badge (recon/exploit/report) — Tab cycles it."""
        UI_STATE["input_mode"] = str(mode or "recon").lower()
        self._tick()

    def start(self) -> None:
        if self._live is None:
            self._live = Live(self._strip(), console=self.console, refresh_per_second=60)
            self._live.start()
            # the heartbeat: rebuild the strip once a second so counters,
            # cost, and the fireteam agent rows stay live (and teams
            # DISAPPEAR the moment they drain) even while nothing prints
            self._refresh_stop.clear()
            self._refresh_thread = threading.Thread(target=self._heartbeat, name="red-strip", daemon=True)
            self._refresh_thread.start()

    def _heartbeat(self) -> None:
        while not self._refresh_stop.wait(1.0):
            self._tick()

    def stop(self) -> None:
        self._refresh_stop.set()
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.stop()
            self._live = None

    def waiting(self, on: bool) -> None:
        """Between events: the strip shows the thinking spinner."""
        self._waiting = bool(on)
        if on and self._waiting_since is None:
            self._waiting_since = time.monotonic()  # TTFT clock starts
        self._tick()

    def reasoning_delta(self, kind: str, text: str) -> None:
        """on_delta sink for the provider stream — the flexing box.

        Called from the provider worker thread; guarded. Content deltas are
        ignored (decisions render complete, exactly as before); reasoning
        deltas grow the panel line by line. Fireteam subagent deltas may
        interleave — cosmetic, tail-capped."""
        if kind != "reasoning" or not text:
            return
        self._stream_buf.append(text)
        if len(self._stream_buf) > 500:
            self._stream_buf = self._stream_buf[-500:]
        self._streaming = True
        if self._waiting_since is not None:
            # first token of this turn: the TTFT proof (seconds, one shot)
            UI_STATE["last_ttft"] = round(time.monotonic() - self._waiting_since, 2)
            self._waiting_since = None
        self._tick()

    def stream_done(self) -> None:
        """Collapse the flexing box — the iteration block takes over the
        transcript (the `said` section renders as before)."""
        if self._stream_buf or self._streaming:
            self._stream_buf = []
            self._streaming = False
            self._waiting_since = None
            self._tick()

    def _stream_panel(self):
        """The opencode-style flexing box: grows as reasoning streams,
        tail-scrolls at the cap, hidden until /think opens it."""
        if not (self._streaming and UI_STATE.get("show_reasoning")):
            return None
        body = Text("".join(self._stream_buf)[-800:], style="dim italic")
        return Panel(
            body,
            box=box.SQUARE,
            border_style=f"dim {GOLD}",
            title=" thinking ",
            title_align="left",
            padding=(0, 1),
        )

    def _tick(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.update(self._strip())

    # ── streamed iteration block ───────────────────────────────────────

    def _section(self, renderable) -> None:
        """One section inside the open iteration: separator rule between
        sections, content printed above the live strip."""
        if self._cur is None:
            self.console.print(renderable)
            return
        if self._cur.sections:
            self.console.print(Rule(style=BORDER, align="left"))
        self._cur.sections += 1
        self.console.print(renderable)

    def _close_open(self, ok: bool | None = None) -> None:
        cur = self._cur
        self._cur = None
        if cur is None or not cur.open:
            return
        color = BORDER if ok is None else (GREEN if ok else RED)
        self.console.print(Rule(style=color, align="left"))

    def _flush(self, border: str = BORDER) -> None:
        ok = None if border == BORDER else (border == GREEN)
        self._close_open(ok)

    def flush_open(self) -> None:
        """Public: flush any buffered iteration (completion / pause)."""
        self._flush()

    def iteration_header(self, n: int, phase: str) -> None:
        self._flush()  # a bookkeeping turn with no output event closes here
        from suijin.modules.providers.lib import USAGE

        tok = int(USAGE.get("input_tokens", 0)) + int(USAGE.get("output_tokens", 0))
        cost = float(USAGE.get("est_cost_usd", 0.0))
        self._cur = _Iteration(n, phase or self.phase, max(0, tok - self._last_tok), max(0.0, cost - self._last_cost))
        self._last_tok, self._last_cost = tok, cost
        self.iteration = n
        self.phase = phase or self.phase
        title = f" #{n} · {self.phase} · +{_fmt_tok(self._cur.dt_tok)} tok · +${self._cur.dt_cost:.4f} "
        self.console.print("")
        self.console.print(Rule(title=title, style=BORDER, align="left"))
        self._cur.open = True
        self.waiting(False)

    def thinking(self, thought: str) -> None:
        if thought:
            self._section(_md(thought, "dim blue"))  # no label — we know what this is
            self._tick()

    def reasoning(self, text: str) -> None:
        if not text:
            return
        UI_STATE["last_reasoning"] = text
        if UI_STATE["show_reasoning"]:
            self._section(_md(text, "cyan"))  # no label
            self._tick()

    def tool(self, tool_name: str, tool_args: dict) -> None:
        if tool_name == "ask_operator":
            return
        parts: list = [Text(f"❯ {tool_name}", style="bold yellow")]
        if tool_name not in _NO_ARGS_TOOLS:
            lexer, get = _LEXERS.get(tool_name, (None, None))
            code = ""
            if get is not None:
                with contextlib.suppress(Exception):
                    code = str(get(tool_args or {}))
            if not code:
                code = _json_args(tool_args or {})
                lexer = "json"
            if code.strip():
                parts.append(_syntax(self.console, code, lexer or "text"))
        if self._cur is not None and self._cur.sections:
            self.console.print(Rule(style=BORDER, align="left"))
        if self._cur is not None:
            self._cur.sections += 1
        for p in parts:
            self.console.print(p)
        self._tick()  # stream: the command is visible while it executes

    def planned_steps(self, steps: list) -> None:
        if not steps:
            return
        lines = [Text(f"plan: {len(steps)} more step(s) queued", style="dim")]
        for s in steps[:4]:
            tn = s.get("tool_name", "?") if isinstance(s, dict) else "?"
            lines.append(Text(f"  - {tn}", style="dim"))
        self._section(Group(*lines))
        self._tick()

    def parse_note(self, attempt: int, max_attempts: int = 3) -> None:
        """The model returned unparseable JSON — show the retry, don't
        let the run die invisibly."""
        self._section(Text(f"response unparseable — asking again ({attempt}/{max_attempts})", style="bold red"))
        self._tick()

    def output(self, text: str, error_class: str = "") -> None:
        out = str(text or "")
        ok = not (is_error(out) or out.startswith("BLOCKED"))
        UI_STATE["last_result_success"] = ok
        if self._cur is None:
            self._cur = _Iteration(self.iteration or 1, self.phase, 0, 0.0)
            self.console.print(Rule(title=f" #{self._cur.n} · {self._cur.phase} ", style=BORDER, align="left"))
            self._cur.open = True
        if self._cur.sections:
            self.console.print(Rule(style=BORDER, align="left"))
        self._cur.sections += 1
        if _is_blocked(out):
            self.console.print(Text(f"BLOCKED  {graceful_error(out)}", style="bold red"))
        elif is_error(out):
            self.console.print(Text(graceful_error(out), style="bold red"))
        else:
            lexer = _guess_output_lexer(out)
            if lexer == "text":
                # plain output — model- and tool-authored text is markdown-ish
                # (notes, findings, advisories); render it as such
                self.console.print(_md(out))
            else:
                self.console.print(_syntax(self.console, out, lexer))
        self._render_loot_into(out)
        self._flush(border=GREEN if ok else RED)
        self.waiting(True)

    # ── loot ───────────────────────────────────────────────────────────

    def _render_loot_into(self, text: str) -> None:
        flags, creds = loot_in(text)
        for f in [f for f in flags if f not in UI_STATE["flags"]]:
            UI_STATE["flags"].append(f)
            self._section(Text(f"Flag collected!  {f}", style=f"bold {GOLD}"))
            self._log_finding("flag", f)
        for kind, v in [c for c in creds if c not in UI_STATE["creds"]]:
            UI_STATE["creds"].append((kind, v))
            self._section(Text(f"Credentials harvested! {kind}: {v}", style="bold green"))
            self._log_finding("credential", f"{kind}: {v[:120]}")

    def loot(self, text: str) -> None:
        if self._cur is not None:
            self._render_loot_into(text)
        else:
            flags, creds = loot_in(text)
            for f in [f for f in flags if f not in UI_STATE["flags"]]:
                UI_STATE["flags"].append(f)
                self.console.print(Text(f"Flag collected!  {f}", style=f"bold {GOLD}"))
            for kind, v in [c for c in creds if c not in UI_STATE["creds"]]:
                UI_STATE["creds"].append((kind, v))
                self.console.print(Text(f"Credentials harvested! {kind}: {v[:60]}", style="bold green"))
        self._tick()

    def _log_finding(self, ftype: str, evidence: str) -> None:
        try:
            from suijin.modules.tools.lib.audit_trail import log_finding

            log_finding(
                ftype,
                severity="high" if ftype == "flag" else "medium",
                endpoint="",
                description=evidence[:200],
                evidence=evidence[:400],
            )
        except Exception:  # noqa: BLE001 — the display line matters more
            pass

    # ── notes (sections inside the open iteration) ─────────────────────

    def _note(self, renderable) -> None:
        if self._cur is not None:
            self._section(renderable)
        else:
            self.console.print(renderable)
        self._tick()

    def supervisor(self, text: str) -> None:
        if text:
            self._note(Text.assemble(("Supervisor  ", "bold magenta"), (str(text), "dim italic")))

    def oracle(self, hypotheses) -> None:
        """hypotheses: list of dicts ({'id','hypothesis',...}) from the oracle,
        a plain list of strings, or a bare string — all render readable."""
        if not hypotheses:
            return
        items = hypotheses if isinstance(hypotheses, list) else [hypotheses]
        rendered = []
        for h in items[:2]:
            if isinstance(h, dict):
                hid = h.get("id", "?")
                hyp = h.get("hypothesis") or h.get("text") or str(h)[:160]
                rendered.append(f"[{hid}] {hyp}" + (f" ({h.get('confidence')})" if h.get("confidence") else ""))
            else:
                rendered.append(str(h)[:200])
        self._note(Text.assemble(("Oracle  ", "bold magenta"), (" // ".join(rendered), "dim italic")))

    def drift(self, text) -> None:
        """text: the drift analyser's RESULT DICT (drift_causes/suggestions)
        or a plain string — both render (the dict crashed Text.assemble in
        the field; the guard saved the run but the warning vanished)."""
        if not text:
            return
        if isinstance(text, dict):
            causes = ", ".join(str(c) for c in text.get("drift_causes", [])[:2]) or "unknown cause"
            sugg = "; ".join(str(s) for s in text.get("suggestions", [])[:3])
            body = causes + (f" — {sugg}" if sugg else "")
        else:
            body = str(text)
        self._note(Text.assemble(("Drift  ", "bold yellow"), (body, "dim")))

    def fireteam(self, text: str) -> None:
        if not text:
            return
        s = str(text)
        if "deployed" in s.lower():
            UI_STATE["fireteams"] += 1
        self._note(Group(Text("fireteam", style="bold magenta"), _md(s)))

    def phase_transition(self, to_phase: str, reason: str = "") -> None:
        self.phase = to_phase or self.phase
        line = Text.assemble(("phase -> ", "bold white"), (self.phase, "bold white"))
        if reason:
            line.append(f"  ({reason})", style="dim")
        self._note(line)

    def ask(self, question: str) -> None:
        """Ask-operator turn: the FULL question as dim markdown (no clips,
        ever), then the Answer prompt prints outside the block with the
        live strip stopped. No thinking section on ask turns."""
        if self._cur is None:
            self._cur = _Iteration(self.iteration or 1, self.phase, 0, 0.0)
            self.console.print(Rule(title=f" #{self._cur.n} · {self._cur.phase} ", style=BORDER, align="left"))
            self._cur.open = True
        self._section(_md(question or "", style="dim"))
        self._close_open()  # the answer prompt must print outside the block

    def done(self, ok: int, total: int, phase: str, cost: float, reason: str) -> None:
        self._flush()
        self.waiting(False)
        self.stop()
        self.console.print(
            f"\n[bold]Done:[/bold] {ok}/{total} steps | phase={escape(phase)} | ${cost:.4f} | {escape(str(reason))}"
        )
        if UI_STATE["flags"]:
            self.console.print(f"[bold {GOLD}]Flags:[/bold {GOLD}] {', '.join(escape(f) for f in UI_STATE['flags'])}")
        if UI_STATE["creds"]:
            self.console.print(f"[bold green]Credentials:[/bold green] {len(UI_STATE['creds'])} captured")

    def failure(self, reason: str, detail: str = "") -> None:
        """Terminal failure (parse_failure / llm_error / provider_failure /
        budget_exhausted / node_crash) — NEVER let a run just vanish."""
        self._flush()
        self.stop()
        body = [Text(reason, style="bold red")]
        if detail:
            body.append(Text(detail, style="dim"))
        self.console.print(Panel(Group(*body), title="engagement ended", title_align="left", border_style=RED))
