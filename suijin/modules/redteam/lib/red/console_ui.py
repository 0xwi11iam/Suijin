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

import collections
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
    "intelligence": "max",  # model intelligence tier (Ctrl+Space cycles; next LLM call)
    "model_label": "",  # "provider model" shown beside the mode badge
    "cursor_on": True,  # the input-box cursor blink (heartbeat toggles)
    "flags": [],
    "creds": [],
    "fireteams": 0,
    "last_reasoning": "",
    "ctx_pct": None,  # live context-window usage (input tokens / window) — the CyberStrike-parity gauge
    "last_result_success": True,
    "poc_running": False,  # catalog_exploit verifier has taken over the loop
    "librarian": 0,  # engagement-memory observations (the librarian thread)
    # verified exploits by id -> severity tier ("low"|"med"|"high"|"crit")
    # — parsed from catalog CONFIRMED output (the EXP/severity strip row)
    "exploits": {},
}


def reset_gauges() -> None:
    """Engagement scope — EVERY per-run key resets. The old partial reset
    left the EXP gauge, the reasoning cache and the mode badge from the
    previous engagement on screen (phantom exploits + a pinned badge)."""
    UI_STATE["flags"] = []
    UI_STATE["creds"] = []
    UI_STATE["exploits"] = {}
    UI_STATE["fireteams"] = 0
    UI_STATE["ctx_pct"] = None
    UI_STATE["ctx_in_tok"] = None
    UI_STATE["ctx_window"] = None
    UI_STATE["poc_running"] = False
    UI_STATE["librarian"] = 0
    UI_STATE["last_reasoning"] = ""
    UI_STATE["last_result_success"] = True
    UI_STATE["input_mode"] = "recon"  # fresh run: the badge re-syncs on the first transition
    UI_STATE["input_buf"] = None
    UI_STATE["suggest_sel"] = None


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
    pre-rendered frame) — the ~10Hz painter re-renders renderables on
    refresh, so the animation is a calm one-frame-per-repaint cycle; the
    1s heartbeat only refreshes the counts."""
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
            task = str(t.get("task", ""))
            state = t.get("state")
            if state == "running":
                # a grid cell holds the LIVE spinner object → native animation;
                # NO truncation — the row flexes as long as the mission needs
                g = Table.grid(padding=(0, 0))
                g.add_row(Text(f"  agent {i}: {task} ", style="dim"), Spinner("dots", style="magenta", speed=1.0))
                rows.append(g)
            elif state == "done":
                ok = bool(t.get("success"))
                mark = "✓" if ok else "✗"
                style = "green" if ok else "red"
                rows.append(Text.assemble((f"  agent {i}: ", "dim"), (task, "dim"), (f" {mark}", style)))
            # queued tasks stay silent — no noise
    return rows[:14]  # height guard only — never truncates a task's text


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
    """Flip the reasoning section visibility. Returns the new state."""
    UI_STATE["show_reasoning"] = not UI_STATE["show_reasoning"]
    if console is not None:
        state = "shown" if UI_STATE["show_reasoning"] else "hidden"
        console.print(f"[dim]  reasoning {state}[/dim]")
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
        return Syntax(code, lexer, theme="monokai", word_wrap=True, background_color="default")
    return Text(code, style="dim")


def _fmt_tok(n: int) -> str:
    """Token counts in M above a million — '2.4M', not '2,400k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


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


SAID = "bright_cyan"  # the SPOKEN content color — dim think, BRIGHT say

# inline markdown tokens for committed content lines (ordered: code/bold/
# link before italic so ** never half-matches as emphasis)
_MD_INLINE = re.compile(
    r"(?P<code>`[^`\n]+?`)"
    r"|(?P<bold>\*\*(?P<bt>.+?)\*\*)"
    r"|(?P<link>\[(?P<lt>[^\]\n]+)\]\((?P<lu>[^)\s]+)\))"
    r"|(?P<ital>\*(?P<it>[^\s*][^*\n]*?)\*)"
)
_MD_HEADER = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")


def _md_line(line: str) -> Text:
    """One committed CONTENT line as inline markdown — bright cyan speech
    with bold/italic/code-span/link styling, headers and bullets dressed.
    Unbalanced markers stay literal (the regex needs the closer)."""
    s = (line or "").rstrip()
    t = Text(style=SAID)
    m = _MD_HEADER.match(s)
    if m:
        t.append(m.group(2), style="bold bright_white")
        return t
    m = _MD_BULLET.match(s)
    if m:
        t.append(m.group(1) + "• ", style=f"bold {GOLD}")
        s = m.group(2)
    pos = 0
    for mm in _MD_INLINE.finditer(s):
        if mm.start() > pos:
            t.append(s[pos : mm.start()])
        if mm.group("code") is not None:
            t.append(mm.group("code")[1:-1], style="bold bright_white")
        elif mm.group("bold") is not None:
            t.append(mm.group("bt"), style="bold")
        elif mm.group("lt") is not None:
            t.append(mm.group("lt"), style="underline")
            t.append(f" ({mm.group('lu')})", style="dim")
        elif mm.group("it") is not None:
            t.append(mm.group("it"), style="italic")
        else:
            t.append(mm.group(0))
        pos = mm.end()
    if pos < len(s):
        t.append(s[pos:])
    return t


def _smart_cut(line: str, row: int) -> int:
    """Row-boundary cut that refuses to split inside an inline markdown
    marker: if the row would end mid-marker (odd backtick/`**` count in
    the row), move the cut back before the marker's start — capped at 24
    chars so genuinely unbalanced text still flows full-width."""
    cut = row
    head = line[:row]
    for marker in ("`", "**"):
        if head.count(marker) % 2 == 1:
            start = head.rfind(marker)
            if start > 0 and row - start <= 24:
                cut = min(cut, start)
    return max(1, cut)


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


# ── stream playback: splitter + typewriter (ALL machinery silent) ──────


class StreamSplitter:
    """Segregates command spans from prose — under the hood, invisible.

    Command spans render as COMPLETE black syntax boxes (never typewrite,
    never show raw): brace-balanced {"action"|"tool"...} JSON objects and
    ``` fenced blocks. Everything between flows through as prose.
    Fed arbitrary fragment boundaries; stateless across feed() calls."""

    def __init__(self):
        self._buf = ""

    def feed(self, text: str) -> list:
        """-> segments: ("text", s) prose or ("box", lang, content) for a
        COMPLETE command span. Open spans and straddling starts stay held
        inside — they resolve on later feeds."""
        self._buf += text
        segments: list = []
        buf = self._buf
        i = 0
        span_open = False
        while i < len(buf):
            fence = buf.find("```", i)
            jstart = self._json_start(buf, i)
            if jstart is None and fence == -1:
                break  # pure prose to the end
            if jstart is not None and (fence == -1 or jstart < fence):
                if jstart > i:
                    segments.append(("text", buf[i:jstart]))
                end = self._json_end(buf, jstart)
                if end is None:
                    i, span_open = jstart, True  # incomplete JSON — hold ALL of it
                    break
                segments.append(("box", "json", buf[jstart:end]))
                i = end
                continue
            if fence > i:
                segments.append(("text", buf[i:fence]))
            close = buf.find("```", fence + 3)
            if close == -1:
                i, span_open = fence, True  # incomplete fence — hold ALL of it
                break
            inner = buf[fence + 3 : close]
            first_nl = inner.find("\n")
            lang = inner[:first_nl].strip() if first_nl >= 0 else ""
            inner = inner[first_nl + 1 :] if first_nl >= 0 else inner
            segments.append(("box", lang or "bash", inner.rstrip("\n")))
            i = close + 3
        tail = buf[i:]
        if span_open:
            self._buf = tail  # an open span never leaks its head into prose
            return segments
        # pure-prose tail: hold any short straddling span-start candidate
        # (a '{' whose '"key' hasn't arrived, a partial fence)
        safe = len(tail)
        cut = max(tail.rfind("{"), tail.rfind("```"))
        if cut != -1 and len(tail) - cut < 48:
            safe = cut
        prose, self._buf = tail[:safe], tail[safe:]
        if prose:
            segments.append(("text", prose))
        return segments

    _JSON_KEYS = ("action", "tool", "auto_actions")

    @classmethod
    def _json_start(cls, buf: str, i: int):
        """Index of a command-JSON opening `{` at/after i, else None."""
        pos = buf.find('{"', i)
        while pos != -1:
            rest = buf[pos + 2 : pos + 40]
            for key in cls._JSON_KEYS:
                if rest.startswith(key):
                    nxt = rest[len(key) : len(key) + 1]
                    if nxt in ('"', '":', "", " "):
                        return pos
            pos = buf.find('{"', pos + 1)
        return None

    @staticmethod
    def _json_end(buf: str, start: int):
        """Index PAST the closing brace of the JSON at start (string- and
        escape-aware); None while incomplete."""
        depth = 0
        in_str = False
        esc = False
        for k in range(start, len(buf)):
            c = buf[k]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
        return None

    def drain(self) -> str:
        """Release whatever is held (an unfinished span at stream end) as
        raw text — content is never silently lost."""
        out, self._buf = self._buf, ""
        return out


class TypewriterStream:
    """Adaptive playback: prose types out at the model's own measured rate.

    60 micro-increment gears span 20 → ~3000 chars/sec (beyond the fastest
    models shipping today), so playback ALWAYS catches up. A 50Hz ticker
    moves rate*dt chars from the pending queue into the live line; when
    the line fills a terminal row it commits to the transcript. Content
    commits as inline MARKDOWN (bright cyan speech); reasoning commits
    dim plain. A KIND SWITCH commits the open line first — the two kinds
    never share a styled line. Each kind owns its OWN splitter, so text
    held by one stream's span candidates never releases under the other
    kind's label. Selection + measurement are invisible — only the line,
    rows, and boxes appear."""

    MIN_RATE = 20.0
    LADDER = sorted({max(20, min(3000, round(20 * (1.09**i)))) for i in range(60)})  # 60 micro-increments
    TICK_HZ = 20.0  # 50Hz fought the Live refresh → terminal control code conflicts → flashing TUI
    CATCHUP_FACTOR = 1.15  # play slightly faster than arrival — always catch up
    BACKLOG_ESCAPE = 3  # rows of backlog that allow faster playback
    MAX_VISUAL = 320.0  # cps — typing reads as typing, not an instant dump
    MAX_ESCAPE = 1400.0  # cps — real backlog drains fast but still visibly

    def __init__(self, ui):
        self._ui = ui
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str]] = []  # (kind, text) prose queue
        self._line = ""
        self._line_kind = ""  # empty until the first char lands (never default to content)
        self._arrivals: collections.deque = collections.deque()  # (monotonic, chars)
        self._rate = self.MIN_RATE
        self._last_cps = 0.0  # held through arrival silence (see _measured_cps)
        # per-kind splitters: a span opened (and held) by REASONING must
        # never release under the CONTENT label — the shared splitter was
        # the random-bright-reasoning leak after code blocks
        self._splitters: dict[str, StreamSplitter] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._playback_paused = False  # ESC ESC: hide the thought NOW
        # serializes whole tick() batches against flush(): the grab+take+
        # line-update happen under ONE lock hold inside a batch, and the
        # batch's prints happen after — flush takes this mutex so it can
        # never swap the queues mid-batch (duplicated/dropped text) or
        # see a half-moved item. Lock order is ALWAYS _tick_mutex → _lock.
        self._tick_mutex = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        # A thread asked to stop may linger up to one tick (50ms) before
        # exiting — the ask flow calls stop() then start() within that
        # window, the old early-return skipped the spawn, and playback
        # died for the rest of the engagement. If a stop was requested,
        # always spawn fresh — with the STOP EVENT BAKED INTO THE NEW
        # THREAD: clearing a shared event before the dying thread wakes
        # would resurrect it alongside the new one (double-speed playback).
        if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="red-typewriter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, stop: threading.Event) -> None:
        dt = 1.0 / self.TICK_HZ
        _fail_logged = False
        while not stop.wait(dt):
            if self._playback_paused:
                continue  # paused: nothing types, nothing commits
            try:
                self.tick(dt)
            except Exception as e:  # noqa: BLE001 — a frozen typewriter is a silent killer
                if not _fail_logged:
                    _fail_logged = True
                    _crash_log("typewriter", e)

    def pause_playback(self) -> None:
        """INSTANT pause: the visible thought line vanishes now (pending
        prose is kept); the ticker stops committing until resume."""
        self._playback_paused = True
        with self._lock:
            self._line, self._line_kind = "", ""
        self._ui._tick(refresh=True)

    def resume_playback(self) -> None:
        self._playback_paused = False

    # ── ingestion ────────────────────────────────────────────────────

    def feed(self, kind: str, text: str) -> None:
        """Kind-routed ingestion: each stream kind owns its OWN splitter,
        so segments (and spans held open across feeds) keep the kind that
        produced them — reasoning held after a code fence can never
        surface as bright content."""
        sp = self._splitters.get(kind)
        if sp is None:
            sp = self._splitters[kind] = StreamSplitter()
        segments = sp.feed(text)
        with self._lock:
            self._arrivals.append((time.monotonic(), len(text)))
            for seg in segments:
                if seg[0] == "text":
                    self._pending.append((kind, seg[1]))
                elif seg[0] == "box":
                    self._pending.append((f"__box__{seg[1]}", seg[2]))

    # ── playback ─────────────────────────────────────────────────────

    def _measured_cps(self) -> float:
        """Arrival rate. Span-floored at 0.5s: the old span-since-first-
        arrival made one big chunk measure at megacycles/sec (span ~1ms),
        snapping the gear to the 3000cps cap = visually instant playback
        (the 'no smooth typewriting' regression); a full 5s window made
        short bursts crawl at MIN_RATE. The floor keeps short streams
        snappy without letting one chunk peg the gear. Arrival SILENCE
        with pending prose HOLDS the last measured rate — a mid-thought
        pause in the stream is not the end of it (flush owns that), and
        the old decay-to-MIN_RATE made post-lull backlog crawl at 20cps
        while the operator watched a frozen line."""
        now = time.monotonic()
        with self._lock:
            arrivals = [(t, n) for (t, n) in self._arrivals if now - t <= 5.0]
            self._arrivals = collections.deque(arrivals)
            if arrivals:
                chars = sum(n for _, n in arrivals)
                span = max(0.5, now - arrivals[0][0])
                self._last_cps = chars / span
            elif not (self._pending or self._line):
                return 0.0
        return min(self._last_cps, float(self.MAX_ESCAPE))

    def live_tokens_per_s(self, window: float = 2.0) -> float:
        """Real generation speed, measured from the provider's own deltas:
        chars arrived in the rolling window / 4 (the codebase's token
        convention). 0.0 when nothing has arrived recently (idle)."""
        now = time.monotonic()
        with self._lock:
            recent = [(t, n) for (t, n) in self._arrivals if now - t <= window]
        if not recent:
            return 0.0
        chars = sum(n for _, n in recent)
        span = max(0.25, now - recent[0][0])
        return (chars / 4.0) / span

    def _backlog(self) -> int:
        with self._lock:
            return sum(len(t) for _, t in self._pending) + len(self._line)

    def _select_rate(self, dt: float) -> float:
        """Micro-increment gear selection — smooth steps, backlog escape.
        The VISUAL CAP keeps playback readable: typing stays visibly
        typing at ≤ MAX_VISUAL cps; only real backlog (the model pages
        ahead) escapes to MAX_ESCAPE so playback never stalls minutes
        behind the stream."""
        target = max(self.MIN_RATE, self._measured_cps() * self.CATCHUP_FACTOR)
        row = max(20, self._ui.console.width - 4)
        cap = self.MAX_ESCAPE if self._backlog() > self.BACKLOG_ESCAPE * row else self.MAX_VISUAL
        target = min(target, cap)
        snap = next((g for g in self.LADDER if g >= target), self.LADDER[-1])
        # smooth: approach the snapped gear at most +18%/-30% per tick
        nxt = min(snap, self._rate * 1.18) if snap > self._rate else max(snap, self._rate * 0.70)
        return max(self.MIN_RATE, nxt)

    def tick(self, dt: float) -> None:
        """One playback step — public for tests. Moves rate*dt chars from
        pending into the line; commits filled rows; emits complete boxes.
        A KIND SWITCH commits the open line first — content never rides a
        dim reasoning line (and vice versa). A paused playback commits
        NOTHING (ESC ESC froze the stream).

        LOCKING: state changes happen under ONE short _lock hold per
        batch; every console.print happens AFTER both locks are released.
        The old code printed committed rows while still holding _lock —
        but console.print (through the Live render hook) needs the Live
        lock, and the strip renderer needs THIS lock: that ABBA pair
        could (and did) freeze the whole TUI mid-stream."""
        if self._playback_paused:
            return
        with self._tick_mutex:
            self._rate = self._select_rate(dt)
            budget = self._rate * dt
            row = max(20, self._ui.console.width - 4)
            while budget > 0:
                rows: list[tuple[str, str]] = []  # (line, kind) — printed after the lock
                box: tuple[str, str] | None = None
                spent = 0
                with self._lock:
                    if not self._pending:
                        break
                    kind, text = self._pending[0]
                    if kind.startswith("__box__"):
                        self._commit_line_locked(rows)  # text above, box below — reading order
                        self._pending.pop(0)
                        box = (kind[len("__box__") :], text)
                    else:
                        if self._line and self._line_kind != kind:
                            self._commit_line_locked(rows)  # kind switch = fresh styled line
                        take = min(len(text), max(1, int(round(budget))), max(1, row - len(self._line)))
                        piece = text[:take]
                        rest = text[take:]
                        if rest:
                            self._pending[0] = (kind, rest)
                        else:
                            self._pending.pop(0)
                        self._line += piece
                        if not self._line_kind:
                            self._line_kind = kind
                        # model-emitted newlines END the row (paragraph breaks): an
                        # embedded \n rendered mid-Text produced the broken
                        # 'w / ork' fragments — commit each line separately so
                        # every one dresses as markdown. Newline commits spend
                        # NO budget (paragraph breaks play free).
                        nl = self._line.rfind("\n")
                        if nl != -1:
                            done, kind_now = self._line[:nl], self._line_kind
                            self._line = self._line[nl + 1 :].lstrip()
                            for seg in done.split("\n"):
                                if seg.strip():
                                    rows.append((seg.rstrip(), kind_now))
                        elif len(self._line) >= row:
                            cut = _smart_cut(self._line, row)  # never split a markdown marker
                            line, kind_now = self._line[:cut].rstrip(), self._line_kind
                            self._line = self._line[cut:].lstrip()
                            if line:
                                rows.append((line, kind_now))
                            if not self._line:
                                self._line_kind = ""
                            spent = take
                        else:
                            spent = take
                # ── I/O strictly OUTSIDE the locks ──
                # refresh the strip renderable FIRST so the re-render that
                # rides this batch's own prints shows the post-commit line
                # (else the strip briefly echoed already-committed text)
                if rows or box is not None:
                    self._ui._tick()
                for ln, k in rows:
                    self._commit_row(ln, k)
                if box is not None:
                    self._emit_box(box[0], box[1])
                    continue  # boxes cost no typewriter budget
                budget -= spent

    # ── emission ─────────────────────────────────────────────────────

    def _commit_line_locked(self, rows: list) -> None:
        """Queue the open live line for commit NOW (kind switch / box /
        flush boundary). Caller holds the lock; printing happens after
        the lock is released."""
        if self._line.strip():
            for seg in self._line.rstrip().split("\n"):
                if seg.strip():
                    rows.append((seg.rstrip(), self._line_kind or "content"))
        self._line, self._line_kind = "", ""

    def _commit_row(self, line: str, kind: str = "content") -> None:
        """Commit one full-width row — the operator contract (final,
        2026-09-17): STREAMED REASONING = dim plain (the monologue),
        THOUGHT = bold cyan (the declarations, via thinking()), SPEAK =
        bright cyan inline markdown. Plain spans only."""
        with contextlib.suppress(Exception):
            if kind == "reasoning":
                self._ui.console.print(Text(line, style="dim"))
            else:
                self._ui.console.print(_md_line(line))

    def _emit_wrapped(self, kind: str, text: str) -> None:
        """Flush-time emission: one merged block — dim plain for the
        reasoning monologue, per-line bright markdown for content."""
        with contextlib.suppress(Exception):
            if kind == "reasoning":
                self._ui.console.print(Text(text, style="dim"))
            else:
                for ln in str(text).split("\n"):
                    if ln.strip():
                        self._ui.console.print(_md_line(ln.rstrip()))

    def _emit_box(self, lang: str, content: str) -> None:
        """A complete command span: black box, syntax-highlighted (the
        plain-white era is over — the visibility bar demands color; the
        lexer falls back intelligently, monokai on default bg).
        IDENTICAL consecutive spans render ONCE — glm's reasoning stream
        often rehearses the exact decision JSON that arrives again as
        content (the duplicate-box field bug)."""
        norm = " ".join(str(content).split())[:2000]
        if norm and norm == getattr(self, "_last_box_norm", None):
            return
        self._last_box_norm = norm
        with contextlib.suppress(Exception):
            lexer = lang if lang not in ("", "bash", "sh") else "bash"
            if lexer in ("json", "javascript", "js", "python", "bash", "sh", "html", "xml"):
                body = self._ui.console.is_terminal and Syntax(
                    str(content)[:2000], lexer, theme="monokai", word_wrap=True, background_color="default"
                )
                if not body:
                    body = Text(str(content)[:2000], style="white")
            else:
                body = Text(str(content)[:2000], style="white")
            self._ui.console.print(
                Panel(
                    body,
                    box=box.SQUARE,
                    border_style="bright_white",
                    title=f" {lang or 'cmd'} ",
                    title_align="left",
                    padding=(0, 1),
                )
            )

    # ── drain / render ───────────────────────────────────────────────

    def flush(self) -> None:
        """stream_done: play everything out instantly — no loss, no litter.
        Each kind's held splitter tail releases UNDER ITS OWN KIND (the
        old single-splitter drain hard-labeled every held tail 'content'
        — held reasoning flushed bright). The live line commits first
        (oldest text), then merged same-kind runs; boxes print complete;
        content is never silently dropped. Holds the tick mutex for the
        whole swap+print: the ticker can never be mid-batch (its grab,
        line-update and prints are atomic against this), so no text is
        duplicated, dropped, or printed out of order."""
        with self._tick_mutex:
            with self._lock:
                pending, self._pending = list(self._pending), []
                line, line_kind = self._line, self._line_kind
                self._line, self._line_kind = "", ""
            if line.strip():
                pending.insert(0, (line_kind or "content", line))
            for kind, splitter in self._splitters.items():
                tail = splitter.drain()
                if tail and tail.strip():
                    pending.append((kind, tail))
            runs: list[tuple[str, list[str]]] = []
            for kind, text in pending:
                if kind.startswith("__box__"):
                    runs.append((kind, [text]))
                elif runs and runs[-1][0] == kind:
                    runs[-1][1].append(text)
                else:
                    runs.append((kind, [text]))
            for kind, chunks in runs:
                if kind.startswith("__box__"):
                    self._emit_box(kind[len("__box__") :], "".join(chunks))
                else:
                    self._emit_wrapped(kind, "".join(chunks))

    def line_renderable(self):
        """The live partial line for the strip — dim reasoning, BRIGHT
        content, block cursor."""
        with self._lock:
            line, kind = self._line, self._line_kind
        if not line:
            return None
        style = "dim" if kind == "reasoning" else SAID
        return Text.assemble(Text(line, style=style), ("▌", style))


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
            "poc_event",
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
        # (dots = 80ms/frame). 1.0 pairs with the ~10fps painter below —
        # one spinner frame per repaint, the calm classic CLI cadence.
        # (3.0 spun at ~37fps, which only made sense against the old
        # 60fps auto-refresh — see _paint_loop for why that had to die.)
        self._spinner = Spinner("dots", style=GOLD, speed=1.0)
        self.iteration = 0
        self.phase = "starting"
        self._cur: _Iteration | None = None
        self._waiting = True
        self._last_tok = 0
        self._refresh_thread: threading.Thread | None = None
        self._refresh_stop = threading.Event()
        self._paused = False  # ESC ESC: the strip shows PAUSED, spinner stops
        # streaming reasoning (the flexing box): deltas land here live
        # streaming: the TypewriterStream (below the class) owns playback.
        # Deltas feed the splitter; prose typewrites through the gear
        # ladder; command spans (JSON actions, fenced blocks) render as
        # complete black syntax boxes. ALL machinery is silent — the screen
        # shows only the typewriter line, committed rows, and boxes.
        self._tw = TypewriterStream(ui=self)
        self._streaming = False  # True between the first delta and stream_done
        self._last_box_norm = None  # duplicate-command-box guard
        self._waiting_since: float | None = None  # think-turn start → TTFT proof
        self._next_report_s: float = float(self._LLM_WAIT_REPORT_S)  # threshold-scheduled still-thinking
        self._last_cost = 0.0

    # ── strip (the ONLY live region — one stable row) ──────────────────

    def _strip(self):
        from suijin.modules.providers.lib import USAGE

        tok = int(USAGE.get("input_tokens", 0)) + int(USAGE.get("output_tokens", 0))
        cost = float(USAGE.get("est_cost_usd", 0.0))
        approx = "" if USAGE.get("priced", True) else "~"
        if self._paused:
            # ESC ESC: PAUSED, no spinner — the engagement is stopped at
            # the operator's chord; the graph winds down in its own time
            left = Text("PAUSED", style="bold yellow")
        elif UI_STATE.get("poc_running"):
            # catalog_exploit: the POC verifier has TAKEN OVER the run
            # loop — the AI is paused until the yaml finishes; input box
            # stays live below
            g = Table.grid(padding=(0, 1))
            g.add_row(self._spinner, Text("RUNNING POC", style="bold yellow"))
            left = g
        elif self._waiting:
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
        lb_n = int(UI_STATE.get("librarian") or 0)
        # MEM = engagement-memory observations written by the librarian
        # thread (was 'LIB' — read as a mystery abbreviation in the field)
        lb_seg = [(" | ", "dim"), (f"MEM {lb_n}", f"bold {GOLD}")] if lb_n else []
        pct = UI_STATE.get("ctx_pct")
        ctx_seg = None
        if pct is not None:
            pf = float(pct)
            style = "green" if pf < 60 else ("yellow" if pf < 85 else "bold red")
            # ABSOLUTE fill beside the pct — 'ctx 2%' beside a cumulative
            # '650k tok' read as broken (spend vs window are DIFFERENT
            # metrics; name both and show the fraction)
            in_t = int(UI_STATE.get("ctx_in_tok") or 0)
            win = int(UI_STATE.get("ctx_window") or 0)
            frac = f" {_fmt_tok(in_t)}/{_fmt_tok(win)}" if win else ""
            ctx_seg = [(" | ", "dim"), (f"ctx {pf:.0f}%{frac}", style)]
        # verified-exploit severity ladder — light blue to red, every
        # color unique to its tier (nothing else in the strip uses these)
        ex = UI_STATE.get("exploits") or {}
        tiers = {"low": 0, "med": 0, "high": 0, "crit": 0}
        for t in ex.values():
            tiers[t] = tiers.get(t, 0) + 1
        sev_seg = []
        for label, key, color in (
            ("LOW", "low", "bright_blue"),
            ("MED", "med", "bright_yellow"),
            ("HIGH", "high", "bright_magenta"),
            ("CRIT", "crit", "bright_red"),
        ):
            sev_seg += [(" | ", "dim"), (f"{label} {tiers[key]}", f"bold {color}")]
        sev_seg += [(" | ", "dim"), (f"EXP {len(ex)}", "bold green")]
        # LIVE GENERATION SPEED — real, from the arrival stream (the
        # same measurement that paces playback); hidden when idle AND
        # when paused (the agent still finishes its turn in the background
        # but showing t/s during pause reads as a UI pause bug)
        tps = 0.0
        if not self._paused:
            with contextlib.suppress(Exception):
                tps = self._tw.live_tokens_per_s()
        tps_seg = [(f"{tps:.0f} t/s", "cyan")] if tps >= 1.0 else []
        right = Text.assemble(
            *tps_seg,
            *sev_seg,
            (" | ", "dim"),
            (f"\u03a3 {_fmt_tok(tok)} tok", "cyan"),
            (" | ", "dim"),
            (f"{approx}${cost:.4f}", "cyan"),
            *(ctx_seg or []),
            (" | ", "dim"),
            (f"CRED {len(UI_STATE['creds'])}", "bold green"),
            *(ft_seg or []),
            *lb_seg,
        )
        t = Table.grid(expand=True, padding=(0, 1))
        t.add_row(left, Text(), right)
        t.columns[1].ratio = 1
        rows = []
        tw = self.typewriter_row()
        if tw is not None:
            rows.append(tw)  # the typewriter line (live partial row)
        rows.append(t)
        rows.extend(_fireteam_agent_rows())
        sug = self._suggestion_row()
        if sug is not None:
            rows.append(sug)  # the command bar, directly above the input box
        rows.append(self._input_box_row())  # the input box is ALWAYS the bottom row
        return Group(*rows)

    # model intelligence tiers — Cmd/Ctrl-style cycler (Ctrl+Space), sent
    # to the provider on the next LLM call (applies between thoughts)
    # the full ladder; the cycler intersects with what the ACTIVE model
    # supports (models.dev reasoning_options) — unsupported tiers never
    # show. Gate resolves lazily: unknown model = the full ladder.
    INTELLIGENCE_TIERS = ("max", "xhigh", "high", "med", "low")

    def _supported_tiers(self) -> tuple:
        import contextlib

        from suijin.modules.redteam.lib.red.console_ui import UI_STATE

        with contextlib.suppress(Exception):
            from suijin.modules.providers.lib.model_meta import supported_effort_levels

            label = str(UI_STATE.get("model_label", "") or "")
            model = label.split()[-1] if label else ""
            prov = label.split()[0] if label else ""
            supported = supported_effort_levels(prov, model) if model else []
            if supported:
                # expand the model's list onto the ladder order + keep aliases working
                aliases = {"medium": "med", "minimum": "low"}
                supported = {aliases.get(v, v) for v in supported}
                ordered = tuple(t for t in self.INTELLIGENCE_TIERS if t in supported)
                return ordered or self.INTELLIGENCE_TIERS
        return self.INTELLIGENCE_TIERS

    # ── slash-command suggestions (opencode-style, 2026-09-17) ─────
    # State: UI_STATE["suggest_sel"] = selection index (None = auto/top),
    # set by arrow keys; the row renders when the buffer starts with "/".
    SUGGESTIONS: dict[str, str] = {}  # "/name" -> one-line description

    def set_suggestion_registry(self, cmds: dict[str, str]) -> None:
        """The merged command registry (pause handlers + /upload + module
        verbs) — set once at engagement boot by the redteamer."""
        self.SUGGESTIONS = {k if k.startswith("/") else f"/{k}": v for k, v in (cmds or {}).items()}

    def _filtered_suggestions(self) -> list[tuple[str, str]]:
        """Commands matching the current buffer: prefix > substring.
        Returns (name, description) pairs, best match first."""
        buf = str(UI_STATE.get("input_buf") or "")
        if not buf.startswith("/"):
            return []
        q = buf[1:].split(" ")[0].lower()  # the command part only
        items = sorted(self.SUGGESTIONS.items())
        if not q:
            return items
        prefix = [(k, v) for k, v in items if k.lstrip("/").startswith(q)]
        substr = [(k, v) for k, v in items if q in k.lstrip("/") and (k, v) not in prefix]
        return prefix + substr

    def _suggestion_row(self):
        """The floating bar above the input box — matching commands with
        the selected one highlighted. None when not applicable (never
        while paused: the pause console owns input then — rendering the
        bar underneath the pause banner crashed the paint)."""
        if self._paused:
            return None
        matches = self._filtered_suggestions()
        if not matches:
            return None
        sel = UI_STATE.get("suggest_sel")
        if sel is None or sel >= len(matches):
            sel = 0
        lines = []
        for i, (name, desc) in enumerate(matches[:8]):
            if i == sel:
                lines.append(Text.assemble(("  › ", "bold cyan"), (f"{name:<14}", "bold cyan"), (desc[:44], "cyan")))
            else:
                lines.append(Text.assemble(("    ", "dim"), (f"{name:<14}", ""), (desc[:44], "dim")))
        if len(matches) > 8:
            lines.append(Text(f"    … {len(matches) - 8} more", style="dim"))
        return Panel(
            Group(*lines),
            box=box.SIMPLE,
            border_style="bright_black",
            padding=(0, 1),
            title=" commands ",
            title_align="left",
        )

    def _input_box_row(self):
        """The operator's prompt — a real white box, ALWAYS the bottom row:
        recon · zai glm-5.3 » ▌type here  ...  max — cursor LEFT of the hint
        badge, provider+model beside it, model-intelligence on the right;
        the cursor BLINKS (heartbeat toggles) so the box reads as focused."""
        mode = str(UI_STATE.get("input_mode", "recon")).lower()
        color = {"recon": "cyan", "exploit": "red", "report": "green"}.get(mode, "cyan")
        model = str(UI_STATE.get("model_label", "") or "")
        intel = str(UI_STATE.get("intelligence", "max"))
        cursor = "▌" if UI_STATE.get("cursor_on", True) else " "
        buf = UI_STATE.get("input_buf")

        left = Text.assemble((mode, f"bold {color}"))
        left.append(f" · {intel}", style=f"bold {'green' if intel == 'max' else 'cyan'}")
        if model:
            left.append(f" · {model}", style="dim")
        left.append(" » ", style=f"bold {GOLD}")
        if buf is not None:
            left.append(str(buf), style="bold white")
            left.append(cursor, style=GOLD)  # typing: cursor TRAILS the text — moves with input
        else:
            left.append(cursor, style=GOLD)  # idle: cursor sits LEFT of the hint
            left.append("type here", style="dim")

        body = left
        return Panel(
            body,
            box=box.SQUARE,
            border_style="bright_white",
            padding=(0, 1),
            expand=True,
        )

    def set_input(self, buf) -> None:
        """Live typing into the box (None = idle hint). Operator keystrokes
        paint immediately — a 100ms echo lag reads as dead input."""
        UI_STATE["input_buf"] = None if buf is None else str(buf)
        self._tick(refresh=True)

    def set_mode(self, mode: str) -> None:
        """The mode badge (recon/exploit/report) — Tab cycles it."""
        UI_STATE["input_mode"] = str(mode or "recon").lower()
        self._tick(refresh=True)

    def start(self) -> None:
        # FULLY idempotent: the ask flow and pause-resume call start()
        # repeatedly — the old version was only Live-idempotent and stacked
        # one heartbeat + typewriter thread per call (thread storm, GIL
        # starvation, the frozen-ui hang)
        if self._live is None:
            # transient: on stop (pause/end) the strip VANISHES cleanly —
            # no stale bottom artifact painted under the pause prompts.
            # auto_refresh=False: WE own the repaint cadence (see
            # _paint_loop) — the old 60fps auto-refresh repainted the
            # full 4-row strip ~43 times/second for the WHOLE engagement
            # (2.6MB of terminal chatter in a 66s rig run): flickering
            # boxes, laggy ssh/tmux, and control-code races with the
            # typewriter's own prints.
            self._live = Live(self._strip(), console=self.console, auto_refresh=False, transient=True)
            self._live.start()
        # a stopped thread may linger up to one 100ms wait before exiting —
        # treat a stop-REQUESTED thread as dead (the typewriter's start()
        # uses the same guard): the ask flow's stop→start could otherwise
        # land inside that window, skip the spawn, and leave the strip
        # unpainted for the rest of the engagement. The stop event is
        # BAKED INTO each spawned thread — clearing a shared event before
        # a dying thread wakes would resurrect it beside the new painter.
        if self._refresh_thread is None or not self._refresh_thread.is_alive() or self._refresh_stop.is_set():
            # the painter + heartbeat: one thread, ~10Hz while anything
            # animates, 1Hz when the strip is static — so counters, cost,
            # the cursor blink and the fireteam rows stay live (and teams
            # DISAPPEAR the moment they drain) without flooding the
            # terminal when nothing moves
            self._refresh_stop = threading.Event()
            stop = self._refresh_stop
            self._refresh_thread = threading.Thread(
                target=self._paint_loop, args=(stop,), name="red-strip", daemon=True
            )
            self._refresh_thread.start()
        # the typewriter ticker: playback thread at 20Hz — WITHOUT this
        # nothing streams live (rows only land at stream_done's flush,
        # which reads as 'clean but no streaming at all'). start() checks
        # its own liveness.
        self._tw.start()

    _LLM_WAIT_REPORT_S = 15  # every 15s of silent thinking, tell the operator

    _tick_fail_logged = False  # one debug line ever — a frozen strip stays diagnosable

    def _strip_animated(self) -> bool:
        """Does the strip contain motion RIGHT NOW? Only those states earn
        ~10fps repaints (spinner, typewriter line, fireteam rows, POC);
        everything else is static and paints at the 1s heartbeat."""
        if self._live is None or self._paused:
            return False
        if self._waiting or UI_STATE.get("poc_running"):
            return True  # the spinner earns its animation
        if self._streaming and (self._tw._line or self._tw._pending):
            return True  # the typewriter line is visibly growing/draining
        with contextlib.suppress(Exception):
            return _fireteam_live_count() > 0
        return False

    def _paint_loop(self, stop: threading.Event) -> None:
        n = 0
        while not stop.wait(0.1):
            n += 1
            try:
                if self._strip_animated():
                    self._tick(refresh=True)
                if n % 10 == 0:  # the 1s heartbeat duties
                    self._heartbeat_duties()
                    if not self._strip_animated():
                        self._tick(refresh=True)  # static: 1fps keeps blink/stats live
            except Exception as e:  # noqa: BLE001 — the strip must never die
                if not EngagementUI._tick_fail_logged:
                    EngagementUI._tick_fail_logged = True
                    _crash_log("paint-loop", e)

    def _heartbeat_duties(self) -> None:
        with contextlib.suppress(Exception):
            UI_STATE["cursor_on"] = not UI_STATE.get("cursor_on", True)  # the blink
        # LLM-wait progress: the spinner says nothing about TIME — a
        # dim transcript line every 15s proves the program is alive.
        # THRESHOLD-scheduled, not `waited % 15 == 0`: heartbeat jitter
        # steps whole seconds (14→16 under load), the modulo never hits,
        # and the operator stares at a silent spinner convinced the UI
        # froze. SUPPRESSED during pause (the PAUSED strip IS the state).
        with contextlib.suppress(Exception):
            if self._waiting and not self._paused and not self._streaming and self._waiting_since is not None:
                waited = time.monotonic() - self._waiting_since
                if waited >= self._next_report_s:
                    self._next_report_s += self._LLM_WAIT_REPORT_S
                    self.console.print(f"[dim]  still thinking… {int(waited)}s[/dim]")

    def stop(self) -> None:
        self._refresh_stop.set()
        self._tw.stop()
        # stuck-state insurance: if the POC verifier died between its start
        # and done events (or the run crashed mid-verification), the strip
        # would show RUNNING POC forever on the next engagement
        UI_STATE["poc_running"] = False
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.stop()
            self._live = None

    def waiting(self, on: bool) -> None:
        """Between events: the strip shows the thinking spinner."""
        self._waiting = bool(on)
        if on and self._waiting_since is None:
            self._waiting_since = time.monotonic()  # TTFT clock starts
            self._next_report_s = float(self._LLM_WAIT_REPORT_S)  # fresh progress cadence
            self._streaming = False  # previous turn's stream is dead (suppresses still-thinking otherwise)
            self._tw.resume_playback()  # unstick the typewriter if it was paused
        self._tick(refresh=True)  # the spinner state flip paints immediately

    def paused_visual(self, on: bool) -> None:
        """ESC ESC instant visual: PAUSED in the strip, spinner stopped,
        thought stream frozen, still-thinking lines suppressed. Resume
        restores the live state."""
        self._paused = bool(on)
        if on:
            self._tw.pause_playback()
        else:
            self._tw.resume_playback()
            self._waiting_since = time.monotonic()  # restart the clock on resume
            self._next_report_s = float(self._LLM_WAIT_REPORT_S)
        self._tick(refresh=True)  # the PAUSED flip is operator-visible: paint NOW

    def guidance_delivered(self, text: str) -> None:
        """Visible confirmation: the think node CONSUMED the operator's
        guidance — no more guessing whether the AI saw it."""
        with contextlib.suppress(Exception):
            from rich.panel import Panel as _Panel

            self.console.print(
                _Panel(
                    f"[bold green]delivered to the AI[/bold green] [dim]{str(text)[:200]}[/dim]",
                    title=" » guidance ",
                    title_align="left",
                    border_style="green",
                    padding=(0, 1),
                )
            )

    def reasoning_delta(self, kind: str, text: str) -> None:
        """on_delta sink for the provider stream — feeds the typewriter.

        Splitter first (per-kind): command spans ({"action"...} JSON,
        ``` fences) are held and rendered as complete black syntax boxes;
        prose flows to the typewriter, which plays it back at the
        measured token rate. Colors: think = dim plain, speak = bright
        cyan inline markdown. The green separator is the ITERATION
        HEADER (one per iteration — no second rule at stream start).
        TTFT records on the first token of any kind. Provider-worker-
        thread safe."""
        if not text:
            return
        if self._waiting_since is not None:
            # first token of this turn: the TTFT proof (seconds, one shot)
            UI_STATE["last_ttft"] = round(time.monotonic() - self._waiting_since, 2)
            self._waiting_since = None
        self._tw.feed(kind, str(text))
        self._streaming = True
        self._tick()

    def stream_done(self) -> None:
        """End the stream: drain everything (remaining prose commits, any
        open command box closes), reset for the next turn (the next
        stream start prints its green separator again)."""
        self._tw.flush()
        self._streaming = False
        self._waiting_since = None
        self._tick()

    def typewriter_row(self):
        """The live partial line for the strip (None when idle)."""
        return self._tw.line_renderable()

    def _tick(self, refresh: bool = False) -> None:
        """Rebuild the strip renderable. refresh=True also paints NOW —
        reserved for operator-visible state flips (pause, waiting, typed
        input); the high-frequency paths stay update-only and the ~10Hz
        painter lands them within 100ms."""
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.update(self._strip(), refresh=refresh)

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
        label = "starting engagement" if n <= 1 else f"iteration {n}"
        title = f" {label} · {self.phase} · +{_fmt_tok(self._cur.dt_tok)} tok · +${self._cur.dt_cost:.4f} "
        self.console.print("")
        # THE one green separator per iteration (operator contract: one
        # line, green, labeled — no stacked yellow+green pair)
        self.console.print(Rule(title=title, style="green", align="left"))
        self._cur.open = True
        self.waiting(False)

    def thinking(self, thought: str) -> None:
        # THE THOUGHT = BOLD CYAN, plain spans (final voice map 2026-09-17):
        # declarations the agent COMMITS to ("Firing the escalation team
        # per ESCALATION READY…", "Let me analyze what I have so far: …")
        # are speech-grade. Never markdown — Rich's Markdown gave the
        # model's backtick spans their own theme color (`PHP/8.3.33` lit
        # up mid-sentence). The raw reasoning MONOLOGUE stays dim via the
        # stream paths; this method only renders the parsed thought field.
        if thought:
            self._section(Text(thought, style="bold cyan"))
            self._tick()

    def reasoning(self, text: str) -> None:
        if not text:
            return
        UI_STATE["last_reasoning"] = text
        if UI_STATE["show_reasoning"]:
            self._section(Text(text, style="dim"))
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

    # the strip's severity gradient — same colors, same meaning
    _SEV_COLORS = {
        "CRITICAL": "bright_red",
        "HIGH": "bright_magenta",
        "MEDIUM": "bright_yellow",
        "LOW": "bright_blue",
    }

    def poc_event(self, event: str, payload: dict) -> None:
        """Sink for the catalog_exploit VERIFIER (installed via
        exploit_catalog.set_poc_sink by the red-teamer). The POC takes
        over the run loop: prominent start line, then per command — a
        numbered Rich panel with the command, a black box with the
        output — strip shows RUNNING POC. Thread-safe; never raises
        (guarded); the run loop itself only WAITS (never-auto-bg'd)."""
        p = payload or {}
        if event == "start":
            UI_STATE["poc_running"] = True
            self._waiting = False  # the spinner is the POC's now
            self.console.print(
                Text.assemble(
                    ("» RUNNING POC ", "bold yellow"),
                    (f"{p.get('eid', '?')} — ", "bold yellow"),
                    (str(p.get("title", ""))[:100], "yellow"),
                    (f"  ({p.get('total', '?')} commands — the AI is paused until the verifier finishes)", "dim"),
                )
            )
            self._tick()
        elif event == "command":
            i, n = p.get("i", "?"), p.get("total", "?")
            self.console.print(
                Panel(
                    Text(str(p.get("cmd", "")), style="bold yellow"),
                    box=box.SQUARE,
                    border_style="yellow",
                    title=f" POC #{i}/{n} ",
                    title_align="left",
                    padding=(0, 1),
                )
            )
        elif event == "output":
            out = str(p.get("text", "")).rstrip()
            if not out:
                out = "(no output)"
            if len(out) > 4000:
                out = out[:3000] + f"\n… [{len(out) - 3500} chars — full output in run-N.log] …" + out[-500:]
            self.console.print(
                Panel(
                    Text(out, style="bright_white"),
                    box=box.SQUARE,
                    border_style="bright_black",
                    style="on black",  # THE black box — dark fill, readable text
                    title=" output ",
                    title_align="left",
                    padding=(0, 1),
                )
            )
        elif event == "done":
            UI_STATE["poc_running"] = False
            self._tick()

    def exploit_verdict(self, result_text: str) -> bool:
        """When catalog_exploit fires, the CLASSED registration renders as
        a prominent verdict panel in the TUI — the agent's own classing,
        e.g. 'CRITICAL CVSS 8.9 : SQL injection in the search parameter'
        with status coloring. Returns True when rendered."""
        import re as _re

        m = _re.search(
            r"(EXP-\d+)\s+(CONFIRMED|AI_CLAIMED|ABANDONED|FAILED_SYNTAX|FAILED_REPRO|DRAFT)\s+—\s+(.+?)(?:\.\s|$)",
            str(result_text or ""),
        )
        if not m:
            return False
        eid, status, rest = m.group(1), m.group(2), m.group(3)
        # THE SEVERITY LEDGER (2026-09-17): catalog_exploit results bypass
        # ui.output() (this panel replaces it), so _track_exploit must fire
        # HERE — otherwise the strip's LOW/MED/HIGH/CRIT/EXP never moves
        # on a confirmed exploit. Only CONFIRMED counts.
        if status == "CONFIRMED":
            sev_m2 = _re.match(r"([A-Z-]+)", rest)
            sev_word = sev_m2.group(1).lower() if sev_m2 else ""
            tier = {"critical": "crit", "high": "high", "medium": "med",
                    "moderate": "med", "low": "low", "informational": "low"}.get(sev_word, "med")
            UI_STATE["exploits"].setdefault(eid, tier)
        sev_m = _re.match(r"([A-Z-]+)\s+CVSS\s+([0-9.]+)\s*:\s*(.+)", rest)
        title = rest
        sev_line = ""
        color = self._SEV_COLORS.get("MEDIUM")
        if sev_m:
            sev, cvss, title = sev_m.group(1), sev_m.group(2), sev_m.group(3)
            sev_line = f"{sev}  CVSS {cvss}"
            color = self._SEV_COLORS.get(sev, "bold cyan")
        status_style = {"CONFIRMED": "bold green", "AI_CLAIMED": "bold yellow"}.get(status, "bold red")
        with contextlib.suppress(Exception):
            body = Text.assemble(
                (f"{sev_line}\n", color) if sev_line else ("", ""),
                (str(title).strip()[:120], "bold white"),
                ("\n", ""),
                (f"{status}", status_style),
                ("  ·  ", "dim"),
                (eid, "dim"),
                ("  ·  NOT terminal-verified" if status == "AI_CLAIMED" else "", "bold yellow"),
            )
            self.console.print(
                Panel(body, title=" vulnerability ", title_align="left", border_style=color, padding=(0, 1))
            )
        return True

    # severity words -> tiers; CVSS floors when no word is present
    _SEV_TIERS = (
        ("critical", "crit"), ("crit", "crit"),
        ("high", "high"),
        ("medium", "med"), ("med", "med"), ("moderate", "med"),
        ("low", "low"), ("informational", "low"), ("info", "low"),
    )

    def _track_exploit(self, out: str) -> None:
        """EXP severity ledger for the strip. Parses the catalog's ACTUAL
        output format (classed_title): 'EXP-001 CONFIRMED — CRITICAL CVSS
        8.9 : title' or 'EXP-001 CONFIRMED — HIGH : title'. The severity
        word comes FIRST in the tail, then optional CVSS. Best-effort."""
        with contextlib.suppress(Exception):
            for m in re.finditer(r"(EXP-\d+)\s+CONFIRMED[^\n]*?[—-]\s*(\w+)", out):
                eid = m.group(1)
                sev_word = m.group(2).lower()
                tier = {"critical": "crit", "crit": "crit",
                        "high": "high",
                        "medium": "med", "med": "med", "moderate": "med",
                        "low": "low", "informational": "low", "info": "low",
                        "note": "low"}.get(sev_word)
                if tier is None:
                    # fallback: look for CVSS in the full line
                    line = out[m.start():m.end() + 80]
                    cv = re.search(r"CVSS\s*([0-9.]+)", line, re.I)
                    if cv:
                        v = float(cv.group(1))
                        tier = "crit" if v >= 9 else "high" if v >= 7 else "med" if v >= 4 else "low"
                    else:
                        tier = "med"  # unlabeled confirmed = med floor
                UI_STATE["exploits"].setdefault(eid, tier)

    def output(self, text: str, error_class: str = "") -> None:
        out = str(text or "")
        ok = not (is_error(out) or out.startswith("BLOCKED"))
        UI_STATE["last_result_success"] = ok
        self._track_exploit(out)
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
        # AGENT-CLASSIFIED credentials (2026-09-17): the agent decides what
        # counts — write_note with credential-flavored content counts as a
        # CRED, catching the weird/proprietary formats our regex misses
        _low = str(text or "")[:300].lower()
        if any(w in _low for w in ("credential", "password", "api key", "apikey", "token found", "secret found", "session hijack", "auth bypass")):
            _note_key = ("agent-classified", _low[:80])
            if _note_key not in UI_STATE["creds"]:
                UI_STATE["creds"].append(_note_key)
                self._section(Text(f"Agent flagged: {_low[:100]}", style="dim green"))

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
        # SPEECH-grade styling (2026-09-17): supervisor guidance reads as
        # said, not thought — dim italic buried it under reasoning. Bright
        # cyan + slight bold, same voice tier as content.
        if text:
            self._note(Text.assemble(("Supervisor  ", "bold magenta"), (str(text), "bold bright_cyan")))

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
        # the input badge FOLLOWS the agent's phase — the mode was pinned to
        # "recon" forever (Tab is still there for a manual override; the
        # next transition re-syncs it)
        _phase = str(self.phase or "").lower()
        for frag, mode in (
            ("post_exploit", "exploit"),
            ("exploit", "exploit"),
            ("report", "report"),
            ("recon", "recon"),
            ("informational", "recon"),
        ):
            if frag in _phase:
                UI_STATE["input_mode"] = mode
                break

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
