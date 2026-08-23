"""Engagement console UI — the red-teamer's live view.

Rich only, no emojis. Design (v2, from the first live field run):

- ONE iteration = ONE Panel:  #3 · informational  (green border on success,
  red on failure, dim for bookkeeping turns). Inside: the thinking line,
  the reasoning sentence underneath it (no label, dim italic — /think
  hides reasoning entirely), the command in a mini-terminal box with
  syntax highlighting, boxed output, loot lines, supervisor/oracle notes.
- The pinned strip at the bottom shows  phase · #iter · tokens · cost ·
  loot counters, and doubles as the thinking spinner between events
  (⠋ thinking… while the LLM works — the npm-spinner feel).
- Errors render GRACEFULLY: raw transport tracebacks are collapsed to
  the meaningful line in a red panel, never a wall of exception text.
"""

from __future__ import annotations

import contextlib
import json
import re

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
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
    "show_reasoning": True,  # reasoning renders under thinking (no label)
    "flags": [],
    "creds": [],
    "fireteams": 0,
    "last_reasoning": "",
    "last_result_success": True,
}

OUTPUT_CLIP = 1600
THOUGHT_CLIP = 500


def toggle_reasoning(console: Console | None = None) -> bool:
    """Flip reasoning visibility. Returns the new state."""
    UI_STATE["show_reasoning"] = not UI_STATE["show_reasoning"]
    if console is not None:
        state = "shown" if UI_STATE["show_reasoning"] else "hidden"
        console.print(f"[dim]  reasoning {state}[/dim]")
        if UI_STATE["show_reasoning"] and UI_STATE["last_reasoning"]:
            console.print(Text(f"  {UI_STATE['last_reasoning'][:800]}", style="dim italic cyan"))
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


def _clip(text: str, n: int) -> str:
    return text[:n] + (f" … (+{len(text) - n} chars)" if len(text) > n else "")


# ── graceful errors ─────────────────────────────────────────────────────


def graceful_error(text: str) -> str:
    """Collapse a raw transport/tool exception blob into the human line.

    'HTTP Error: HTTPSConnectionPool(...): Max retries exceeded ... (
    Caused by NameResolutionNameError(...))'  ->  'HTTP Error: could not
    resolve drforst.org (DNS)'. Never a wall of traceback."""
    t = (text or "").strip()
    t = re.sub(r"Traceback \(most recent call last\):.*?(?=\w+Error|\w+Exception)", "", t, flags=re.S)
    t = " ".join(t.split())
    # DNS resolution failures — the most common wall of text
    m = re.search(r"Failed to resolve ['\"]?([^'\"()\s]+)", t)
    if m:
        return f"could not resolve {m.group(1)} (DNS)"
    m = re.search(r"NameResolutionError\(['\"]?Failed to resolve (\S+)", t)
    if m:
        return f"could not resolve {m.group(1)} (DNS)"
    # connection refused / timeouts
    m = re.search(r"Connection to ([\d.]+)(?: port (\d+))? (failed|refused|timed out)", t)
    if m:
        port = f":{m.group(2)}" if m.group(2) else ""
        return f"connection to {m.group(1)}{port} {m.group(3)}"
    m = re.search(r"Max retries exceeded with url (\S+)", t)
    if m:
        return f"unreachable: {m.group(1)[:80]}"
    m = re.search(r"ReadTimeoutError.*?timeout", t) or re.search(r"timed out", t)
    if m:
        return "request timed out"
    # command-not-found and friends
    m = re.search(r"(?:sh: )?(\S+): command not found", t)
    if m:
        return f"{m.group(1)} not installed"
    # generic: first sentence of the last exception line
    m = re.search(r"(\w+(?:Error|Exception))[:\s](.{10,160})", t)
    if m:
        return f"{m.group(1)}: {m.group(2).strip()}"
    return _clip(t, 200)


def is_error(text: str) -> bool:
    o = (text or "").lstrip()
    return o.startswith(("Error:", "Tool error:", "Tool Error", "HTTP Error", "Execution Fault", "Traceback"))


# ── per-tool syntax highlighting ────────────────────────────────────────


def _bash_args(a: dict) -> str:
    return str(a.get("cmd") or a.get("command") or a.get("script") or "")


def _json_args(a: dict) -> str:
    try:
        return json.dumps(a, indent=2, default=str)
    except Exception:  # noqa: BLE001 — never break a render
        return str(a)


_LEXERS: dict[str, tuple[str, object]] = {
    "execute_terminal": ("bash", _bash_args),
    "http_request": ("json", _json_args),
    "nmap": ("bash", lambda a: "nmap " + str(a.get("target", "")) + " " + str(a.get("flags", "")).strip()),
    "gobuster": ("bash", lambda a: "gobuster " + str(a.get("args", a.get("flags", "")))),
    "ffuf": ("bash", lambda a: "ffuf " + str(a.get("args", a.get("flags", "")))),
    "sqlmap": ("bash", lambda a: "sqlmap " + str(a.get("args", a.get("cmd", "")))),
    "nikto": ("bash", lambda a: "nikto " + str(a.get("args", a.get("target", "")))),
    "hydra": ("bash", lambda a: "hydra " + str(a.get("args", a.get("cmd", "")))),
    "msf_command": ("ruby", _bash_args),
    "write_file": ("python", lambda a: str(a.get("content", ""))),
    "write_note": ("markdown", lambda a: str(a.get("content", ""))),
    "search_cve": ("text", lambda a: f"software={a.get('software', '')} version={a.get('version', '')}"),
}

_BLOCKED_PREFIXES = ("TOOL NOT FOUND", "policy:", "BLOCKED", "Error: tool")


def _is_blocked(out: str) -> bool:
    o = (out or "").lstrip()
    return any(o.startswith(p) for p in _BLOCKED_PREFIXES)


def _mini_terminal(console: Console, tool_name: str, tool_args: dict) -> object:
    """The command in a small terminal-looking box, syntax highlighted."""
    lexer, get = _LEXERS.get(tool_name, (None, None))
    code = ""
    if get is not None:
        with contextlib.suppress(Exception):
            code = str(get(tool_args or {}))
    if not code:
        code = _json_args(tool_args or {})
        lexer = "json"
    if not code.strip():
        return None
    if console.is_terminal and lexer not in (None, "text"):
        with contextlib.suppress(Exception):
            body = Syntax(code, lexer, theme="monokai", word_wrap=True, background_color="default")
            return Panel(
                body,
                title=f"❯ {escape(tool_name)}",
                title_align="left",
                border_style=BORDER,
                padding=(0, 1),
            )
    return Panel(
        Text(code[:800], style="dim"),
        title=f"❯ {escape(tool_name)}",
        title_align="left",
        border_style=BORDER,
        padding=(0, 1),
    )


class _Iteration:
    """Buffers one iteration's renderables; flushed as a single Panel."""

    def __init__(self, n: int, phase: str):
        self.n = n
        self.phase = phase
        self.parts: list = []
        self.has_tool = False

    def add(self, renderable) -> None:
        self.parts.append(renderable)


class EngagementUI:
    """Owns the pinned strip; buffers each iteration into one Panel."""

    def __init__(self, console: Console, objective: str = ""):
        self.console = console
        self.objective = objective
        self._live: Live | None = None
        self._spinner = Spinner("dots", style=GOLD, speed=0.4)
        self.iteration = 0
        self.phase = "starting"
        self._cur: _Iteration | None = None
        self._waiting = True

    # ── strip (doubles as the thinking spinner) ────────────────────────

    def _strip(self) -> Table:
        from suijin.modules.providers.lib import USAGE

        tok = int(USAGE.get("input_tokens", 0)) + int(USAGE.get("output_tokens", 0))
        tok_str = f"{tok / 1000:.1f}k" if tok >= 1000 else str(tok)
        cost = float(USAGE.get("est_cost_usd", 0.0))
        approx = "" if USAGE.get("priced", True) else "~"
        if self._waiting and self.iteration == 0:
            left = self._spinner
        elif self._waiting:
            g = Table.grid(padding=(0, 1))
            g.add_row(self._spinner, Text(self.phase, style="dim"))
            left = g
        else:
            left = Text(f"suijin {self.phase} #{self.iteration}", style=f"bold {GOLD}")
        right = Text.assemble(
            (f"{tok_str} tok", "cyan"),
            (" | ", "dim"),
            (f"{approx}${cost:.4f}", "cyan"),
            *([(" | ", "dim"), (f"FLAG {len(UI_STATE['flags'])}", f"bold {GOLD}")] if UI_STATE["flags"] else []),
            *([(" | ", "dim"), (f"CRED {len(UI_STATE['creds'])}", "bold green")] if UI_STATE["creds"] else []),
            *([(" | ", "dim"), (f"FT {UI_STATE['fireteams']}", "bold magenta")] if UI_STATE["fireteams"] else []),
        )
        t = Table.grid(expand=True, padding=(0, 1))
        t.add_row(left, Text(), right)
        t.columns[1].ratio = 1
        return t

    def start(self) -> None:
        if self._live is None:
            self._live = Live(self._strip(), console=self.console, refresh_per_second=4)
            self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.stop()
            self._live = None

    def waiting(self, on: bool) -> None:
        """Between events: the strip shows the thinking spinner."""
        self._waiting = bool(on)
        self._tick()

    def _tick(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.update(self._strip())

    # ── iteration buffering ────────────────────────────────────────────

    def _flush(self, border: str = BORDER, title_suffix: str = "") -> None:
        cur = self._cur
        self._cur = None
        if cur is None or not cur.parts:
            return
        title = f" #{cur.n} · {cur.phase}{title_suffix} "
        panel = Panel(Group(*cur.parts), title=title, title_align="left", border_style=border, padding=(0, 1))
        self.console.print(panel)

    def flush_open(self) -> None:
        """Public: flush any buffered iteration (e.g. on completion)."""
        self._flush()

    def iteration_header(self, n: int, phase: str) -> None:
        self._flush()  # bookkeeping turn with no output event flushes here
        self.iteration = n
        self.phase = phase or self.phase
        self._cur = _Iteration(n, self.phase)
        self.waiting(False)

    def thinking(self, thought: str) -> None:
        if thought and self._cur is not None:
            self._cur.add(Text(f"thinking  {_clip(thought, THOUGHT_CLIP)}", style="cyan"))

    def reasoning(self, text: str) -> None:
        if not text:
            return
        UI_STATE["last_reasoning"] = text
        if UI_STATE["show_reasoning"] and self._cur is not None:
            self._cur.add(Text(_clip(text, 800), style="dim italic cyan"))

    def tool(self, tool_name: str, tool_args: dict) -> None:
        if tool_name == "ask_operator" or self._cur is None:
            return
        self._cur.has_tool = True
        box = _mini_terminal(self.console, tool_name, tool_args or {})
        if box is not None:
            self._cur.add(box)

    def planned_steps(self, steps: list) -> None:
        if steps and self._cur is not None:
            lines = [Text(f"plan: {len(steps)} more step(s) queued", style="dim")]
            for s in steps[:4]:
                tn = s.get("tool_name", "?") if isinstance(s, dict) else "?"
                lines.append(Text(f"  - {tn}", style="dim"))
            from rich.console import Group as _G

            self._cur.add(_G(*lines))

    def output(self, text: str, error_class: str = "") -> None:
        out = str(text or "")
        ok = not (is_error(out) or out.startswith("BLOCKED"))
        UI_STATE["last_result_success"] = ok
        if self._cur is None:
            self._cur = _Iteration(self.iteration or 1, self.phase)
        if _is_blocked(out):
            self._cur.add(Text(f"BLOCKED  {graceful_error(out)}", style="bold red"))
        elif is_error(out):
            self._cur.add(
                Panel(
                    Text(graceful_error(out), style="red"),
                    title="error",
                    title_align="left",
                    border_style=RED,
                    padding=(0, 1),
                )
            )
        else:
            self._cur.add(
                Panel(
                    Text(_clip(out, OUTPUT_CLIP), style="dim"),
                    title="output",
                    title_align="left",
                    border_style=BORDER,
                    padding=(0, 1),
                )
            )
        self._render_loot_into(self._cur, out)
        self._flush(border=GREEN if ok else RED)
        self.waiting(True)

    def loot(self, text: str) -> None:
        if self._cur is None:
            self.loot_standalone(text)
            return
        self._render_loot_into(self._cur, text)
        self._tick()

    def _render_loot_into(self, it: _Iteration, text: str) -> None:
        flags, creds = loot_in(text)
        new_flags = [f for f in flags if f not in UI_STATE["flags"]]
        new_creds = [c for c in creds if c not in UI_STATE["creds"]]
        for f in new_flags:
            UI_STATE["flags"].append(f)
            it.add(Text(f"Flag collected!  {f}", style=f"bold {GOLD}"))
            self._log_finding("flag", f)
        for kind, v in new_creds:
            UI_STATE["creds"].append((kind, v))
            it.add(Text(f"Credentials harvested! {kind}: {v[:60]}", style="bold green"))
            self._log_finding("credential", f"{kind}: {v[:120]}")

    def loot_standalone(self, text: str) -> None:
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

    # ── notes (buffered into the open iteration) ───────────────────────

    def _note(self, renderable) -> None:
        if self._cur is not None:
            self._cur.add(renderable)
        else:
            self.console.print(renderable)

    def supervisor(self, text: str) -> None:
        if text:
            self._note(Text.assemble(("Supervisor  ", "bold magenta"), (_clip(text, 300), "dim italic")))

    def oracle(self, hypotheses) -> None:
        if hypotheses:
            h = hypotheses if isinstance(hypotheses, list) else [hypotheses]
            self._note(Text.assemble(("Oracle  ", "bold magenta"), (_clip(str(h[0]), 240), "dim italic")))

    def drift(self, text: str) -> None:
        if text:
            self._note(Text.assemble(("Drift  ", "bold yellow"), (_clip(text, 240), "dim")))

    def fireteam(self, text: str) -> None:
        if not text:
            return
        s = str(text)
        if "deployed" in s.lower():
            UI_STATE["fireteams"] += 1
        self._note(Text(_clip(s, 400), style="bold magenta"))
        self._tick()

    def phase_transition(self, to_phase: str, reason: str = "") -> None:
        self.phase = to_phase or self.phase
        line = Text.assemble(("phase -> ", "bold white"), (self.phase, "bold white"))
        if reason:
            line.append(f"  ({_clip(reason, 120)})", style="dim")
        self._note(line)
        self._tick()

    def ask(self, question: str) -> None:
        if self._cur is None:
            self._cur = _Iteration(self.iteration or 1, self.phase)
        self._cur.add(Text(f"Question  {_clip(question, 300)}", style=f"bold {GOLD}"))
        self._flush()  # the answer prompt must print outside the panel

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
