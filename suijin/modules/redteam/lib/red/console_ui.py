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
    "show_reasoning": True,  # 'said' section renders by default
    "flags": [],
    "creds": [],
    "fireteams": 0,
    "last_reasoning": "",
    "last_result_success": True,
}


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
    deadline = _time.monotonic() + timeout_s
    run_box.take_guidance()  # drain stale lines queued before the question
    while _time.monotonic() < deadline:
        lines = run_box.take_guidance()
        if lines:
            return lines[0].strip()
        _time.sleep(0.15)
    return ""


def toggle_reasoning(console: Console | None = None) -> bool:
    """Flip the 'said' section visibility. Returns the new state."""
    UI_STATE["show_reasoning"] = not UI_STATE["show_reasoning"]
    if console is not None:
        state = "shown" if UI_STATE["show_reasoning"] else "hidden"
        console.print(f"[dim]  said {state}[/dim]")
        if UI_STATE["show_reasoning"] and UI_STATE["last_reasoning"]:
            console.print(Text(UI_STATE["last_reasoning"], style="bright_cyan"))
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


def _md(text: str, style: str = "none") -> Markdown:
    """Markdown render for model-authored text (thinking/said/findings/
    plain tool output) — the model writes markdown; show it as markdown."""
    return Markdown(text or "", code_theme="monokai", style=style, hyperlinks=False)


_BLOCKED_PREFIXES = ("TOOL NOT FOUND", "policy:", "BLOCKED", "Error: tool")


def _is_blocked(out: str) -> bool:
    o = (out or "").lstrip()
    return any(o.startswith(p) for p in _BLOCKED_PREFIXES)


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
        self._live: Live | None = None
        self._spinner = Spinner("dots", style=GOLD, speed=0.4)
        self.iteration = 0
        self.phase = "starting"
        self._cur: _Iteration | None = None
        self._waiting = True
        self._last_tok = 0
        self._last_cost = 0.0

    # ── strip (the ONLY live region — one stable row) ──────────────────

    def _strip(self) -> Table:
        from suijin.modules.providers.lib import USAGE

        tok = int(USAGE.get("input_tokens", 0)) + int(USAGE.get("output_tokens", 0))
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
            (f"{_fmt_tok(tok)} tok", "cyan"),
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
            self._section(Group(Text("thinking", style="dim blue"), _md(thought, "dim")))
            self._tick()

    def reasoning(self, text: str) -> None:
        if not text:
            return
        UI_STATE["last_reasoning"] = text
        if UI_STATE["show_reasoning"]:
            self._section(Group(Text("said:", style="bright_cyan"), _md(text)))
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
            self._note(Text.assemble(("Supervisor  ", "bold magenta"), (text, "dim italic")))

    def oracle(self, hypotheses) -> None:
        if hypotheses:
            h = hypotheses if isinstance(hypotheses, list) else [hypotheses]
            self._note(Text.assemble(("Oracle  ", "bold magenta"), (str(h[0]), "dim italic")))

    def drift(self, text: str) -> None:
        if text:
            self._note(Text.assemble(("Drift  ", "bold yellow"), (text, "dim")))

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
        """Ask-operator turn: ONE dim context line, then the Answer prompt
        prints outside the block. No thinking line, no question wall —
        the operator asked for exactly: `Answer:` and a place to type."""
        if self._cur is None:
            self._cur = _Iteration(self.iteration or 1, self.phase, 0, 0.0)
            self.console.print(Rule(title=f" #{self._cur.n} · {self._cur.phase} ", style=BORDER, align="left"))
            self._cur.open = True
        first_line = (question or "").strip().split("\n")[0]
        if first_line:
            self._section(Text(first_line[:140], style="dim"))
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
