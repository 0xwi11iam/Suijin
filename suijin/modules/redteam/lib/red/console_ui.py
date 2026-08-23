"""Engagement console UI — the red-teamer's live view.

Rich only, no emojis. A scrolling transcript (the repo's house style) plus a
pinned in-line Live strip (battle.py pattern) showing iteration, phase,
tokens, cost, flags, creds, fireteams. Transcript blocks per iteration:

    #6 + recon
      thinking    Crafting an XSS payload to test the search field...
        :: why :: Search reflects input unencoded; testing stored-XSS...
      > execute_terminal
      [ bash ]  curl -s "http://t/search" --data-urlencode "q=<script>..."
      [ output ] Status: 200 ... reflected verbatim
      Credentials harvested!  AWS_TOKEN=AKIA...
      Flag collected!         FLAG{...}

`reasoning` (the ":: why ::" line) is hidden unless toggled with /think —
on toggle the last reasoning is re-printed immediately. Every tool output is
loot-scanned: FLAG{...} and credential patterns become colored lines and
strip counters, and are recorded as audit findings live.
"""

from __future__ import annotations

import contextlib
import json
import re

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

GOLD = "#e6b47c"  # red-team accent (repo convention)
BORDER = "#30363d"

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
    "show_reasoning": False,
    "flags": [],  # ordered unique
    "creds": [],  # (kind, value)
    "fireteams": 0,
    "last_reasoning": "",
    "last_result_success": True,
}

OUTPUT_CLIP = 2000
THOUGHT_CLIP = 500


def toggle_reasoning(console: Console | None = None) -> bool:
    """Flip the reasoning visibility toggle. Returns the new state."""
    UI_STATE["show_reasoning"] = not UI_STATE["show_reasoning"]
    if console is not None:
        state = "shown" if UI_STATE["show_reasoning"] else "hidden"
        console.print(f"[dim]  reasoning {state}[/dim]")
        if UI_STATE["show_reasoning"] and UI_STATE["last_reasoning"]:
            _print_reasoning(console, UI_STATE["last_reasoning"])
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


def _print_reasoning(console: Console, text: str) -> None:
    console.print(Text(f"  :: why :: {text[:1000]}", style="dim italic cyan"))


def _clip(text: str, n: int) -> str:
    return text[:n] + (f"... [+{len(text) - n} chars]" if len(text) > n else "")


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


def _render_code(console: Console, tool_name: str, tool_args: dict) -> None:
    """The `> tool` line + syntax-highlighted command block."""
    console.print(f"  [bold yellow]> {escape(tool_name)}[/bold yellow]")
    lexer, get = _LEXERS.get(tool_name, (None, None))
    code = ""
    if get is not None:
        try:
            code = str(get(tool_args or {}))
        except Exception:  # noqa: BLE001 — highlight must never break rendering
            code = ""
    if not code:
        try:
            code = _json_args(tool_args or {})
            lexer = "json"
        except Exception:  # noqa: BLE001
            return
    if not code.strip():
        return
    if console.is_terminal:
        try:
            console.print(
                Syntax(
                    code,
                    lexer or "text",
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                    background_color="default",
                ),
            )
        except Exception:  # noqa: BLE001 — exotic lexers on odd rich builds
            console.print(Text(f"  {code[:600]}", style="dim"))
    else:
        console.print(Text(f"  {code[:600]}", style="dim"))


# ── blocked-output detection (policy/mode/not-found strings) ────────────

_BLOCKED_PREFIXES = (
    "TOOL NOT FOUND",
    "policy:",
    "BLOCKED",
    "Error: tool",
)


def _is_blocked(out: str) -> bool:
    o = (out or "").lstrip()
    return any(o.startswith(p) for p in _BLOCKED_PREFIXES)


class EngagementUI:
    """Owns the pinned Live strip; every method prints transcript lines."""

    def __init__(self, console: Console, objective: str = ""):
        self.console = console
        self.objective = objective
        self._live: Live | None = None
        self.iteration = 0
        self.phase = "?"

    # ── strip ───────────────────────────────────────────────────────────

    def _strip(self) -> Table:
        from suijin.modules.providers.lib import USAGE

        tok = int(USAGE.get("input_tokens", 0)) + int(USAGE.get("output_tokens", 0))
        tok_str = f"{tok / 1000:.1f}k" if tok >= 1000 else str(tok)
        cost = float(USAGE.get("est_cost_usd", 0.0))
        approx = "" if USAGE.get("priced", True) else "~"
        t = Table.grid(expand=True, padding=(0, 1))
        left = Text(f"suijin {self.phase} #{self.iteration}", style=f"bold {GOLD}")
        right = Text.assemble(
            (f"{tok_str} tok", "cyan"),
            (" | ", "dim"),
            (f"{approx}${cost:.4f}", "cyan"),
            *([(" | ", "dim"), (f"FLAG {len(UI_STATE['flags'])}", f"bold {GOLD}")] if UI_STATE["flags"] else []),
            *([(" | ", "dim"), (f"CRED {len(UI_STATE['creds'])}", "bold green")] if UI_STATE["creds"] else []),
            *([(" | ", "dim"), (f"FT {UI_STATE['fireteams']}", "bold magenta")] if UI_STATE["fireteams"] else []),
        )
        t.add_row(left, Text(), right)  # middle stretches
        t.columns[1].ratio = 1
        return t

    def start(self) -> None:
        if self._live is None:
            self._live = Live(self._strip(), console=self.console, refresh_per_second=2)
            self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.stop()
            self._live = None

    def _tick(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.update(self._strip())

    # ── transcript blocks ───────────────────────────────────────────────

    def iteration_header(self, n: int, phase: str) -> None:
        self.iteration = n
        self.phase = phase or self.phase
        ok = UI_STATE["last_result_success"]
        mark = "[green]+[/green]" if ok else "[red]![/red]"
        self.console.print(f"\n[bold white]#{n}[/bold white] {mark} [dim]{escape(self.phase)}[/dim]")
        self._tick()

    def thinking(self, thought: str) -> None:
        if thought:
            self.console.print(f"  [cyan]thinking  {escape(_clip(thought, THOUGHT_CLIP))}[/cyan]")

    def reasoning(self, text: str) -> None:
        if not text:
            return
        UI_STATE["last_reasoning"] = text
        if UI_STATE["show_reasoning"]:
            _print_reasoning(self.console, text)

    def tool(self, tool_name: str, tool_args: dict) -> None:
        if tool_name == "ask_operator":
            return  # rendered as its own block, not a dict dump
        _render_code(self.console, tool_name, tool_args or {})

    def planned_steps(self, steps: list) -> None:
        if not steps:
            return
        lines = [f"  [dim]plan: {len(steps)} more step(s) queued[/dim]"]
        for s in steps[:4]:
            tn = s.get("tool_name", "?") if isinstance(s, dict) else "?"
            lines.append(f"  [dim]  - {escape(str(tn))}[/dim]")
        self.console.print("\n".join(lines))

    def output(self, text: str, error_class: str = "") -> None:
        out = str(text or "")
        UI_STATE["last_result_success"] = not (
            out.startswith("Error:") or out.startswith("Tool error:") or out.startswith("Tool Error")
        )
        body = _clip(out, OUTPUT_CLIP)
        if _is_blocked(out):
            self.console.print(f"  [bold red]BLOCKED[/bold red] [dim]{escape(_clip(out, 400))}[/dim]")
        else:
            self.console.print(
                Panel(
                    Text(body, style="dim", no_wrap=False),
                    title="output",
                    title_align="left",
                    border_style=BORDER,
                    expand=False,
                )
            )
        self.loot(out)

    def loot(self, text: str) -> None:
        flags, creds = loot_in(text)
        new_flags = [f for f in flags if f not in UI_STATE["flags"]]
        new_creds = [c for c in creds if c not in UI_STATE["creds"]]
        if new_flags:
            UI_STATE["flags"].extend(new_flags)
            for f in new_flags:
                self.console.print(f"  [bold {GOLD}]Flag collected![/bold {GOLD}]  {escape(f)}")
                self._log_finding("flag", f)
        if new_creds:
            UI_STATE["creds"].extend(new_creds)
            for kind, v in new_creds:
                self.console.print(
                    f"  [bold green]Credentials harvested![/bold green] {escape(kind)}: {escape(v[:60])}"
                )
                self._log_finding("credential", f"{kind}: {v[:120]}")
        if new_flags or new_creds:
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

    def supervisor(self, text: str) -> None:
        if text:
            self.console.print(
                f"  [bold magenta]Supervisor:[/bold magenta] [dim italic]{escape(_clip(text, 300))}[/dim italic]"
            )

    def oracle(self, hypotheses) -> None:
        if hypotheses:
            h = hypotheses if isinstance(hypotheses, list) else [hypotheses]
            self.console.print(
                f"  [bold magenta]Oracle:[/bold magenta] [dim italic]{escape(_clip(str(h[0]), 240))}[/dim italic]"
            )

    def drift(self, text: str) -> None:
        if text:
            self.console.print(f"  [bold yellow]Drift:[/bold yellow] [dim]{escape(_clip(text, 240))}[/dim]")

    def fireteam(self, text: str) -> None:
        if not text:
            return
        s = str(text)
        if "deployed" in s.lower():
            UI_STATE["fireteams"] += 1
        self.console.print(f"  [bold magenta]{escape(_clip(s, 400))}[/bold magenta]")
        self._tick()

    def phase_transition(self, to_phase: str, reason: str = "") -> None:
        self.phase = to_phase or self.phase
        line = f"  [bold white]phase -> {escape(self.phase)}[/bold white]"
        if reason:
            line += f" [dim]({escape(_clip(reason, 120))})[/dim]"
        self.console.print(line)
        self._tick()

    def note_line(self, text: str, style: str = "dim") -> None:
        self.console.print(f"  [{style}]{escape(_clip(text, 400))}[/{style}]")

    def ask(self, question: str) -> None:
        self.console.print(f"  [bold {GOLD}]Question  {escape(_clip(question, 300))}[/bold {GOLD}]")

    def done(self, ok: int, total: int, phase: str, cost: float, reason: str) -> None:
        self.stop()
        self.console.print(
            f"\n[bold]Done:[/bold] {ok}/{total} steps | phase={escape(phase)} | ${cost:.4f} | {escape(str(reason))}"
        )
        if UI_STATE["flags"]:
            self.console.print(f"[bold {GOLD}]Flags:[/bold {GOLD}] {', '.join(escape(f) for f in UI_STATE['flags'])}")
        if UI_STATE["creds"]:
            self.console.print(f"[bold green]Credentials:[/bold green] {len(UI_STATE['creds'])} captured")
