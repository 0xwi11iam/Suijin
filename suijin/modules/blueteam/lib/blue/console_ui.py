"""Blue console UI — the SOC operator's live view (BF3).

Red-parity visual language, blue doctrine: event panels (request ->
verdict -> action), thinking/said sections, a pinned live strip
(req · threats · blocked · deceived · watchers · cost), a pause
console (/block /unblock /state /tarpits /report /health /quit),
crash logs, and termination banners. Rich only, no emojis. Every
public render method is guarded — a render bug must never kill a
defense session.
"""

from __future__ import annotations

import contextlib
import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

BLUE = "#58a6ff"  # blue-team accent (repo convention)
BORDER = "#30363d"
RED = "#ff5555"
GREEN = "#3fb950"
GOLD = "#e6b47c"


def _fmt_n(n) -> str:
    return f"{int(n or 0)}"


class _GuardedConsole:
    """Wraps a Console so blue code can print through crashes."""

    def __init__(self, console: Console):
        self._c = console

    def print(self, *a, **kw):
        with contextlib.suppress(Exception):
            self._c.print(*a, **kw)

    @property
    def width(self):
        return self._c.width

    @property
    def is_terminal(self):
        return self._c.is_terminal


class BlueConsoleUI:
    """The blue session's console: event blocks + pinned strip + pause menu."""

    def __init__(self, console: Console, target: str = "", out=None):
        self.console = _GuardedConsole(console)
        self._raw = console  # for Live
        self._out = out if out is not None else console  # transcript echo
        self.target = target
        self._spinner = Spinner("dots", style=BLUE, speed=3.0)
        self._live: Live | None = None
        self._waiting = True
        # strip state
        self.requests = 0
        self.detected = 0
        self.blocked = 0
        self.deceived = 0
        self.tarpitted = 0
        self.watchers = 0
        self.cost_usd = 0.0
        self.started = time.monotonic()
        self._open_event: dict | None = None
        self._sections = 0

    # ── strip ───────────────────────────────────────────────────────────

    def _strip(self) -> Table:
        up = int(time.monotonic() - self.started)
        up_str = f"{up // 60}m{up % 60:02d}s" if up >= 60 else f"{up}s"
        if self._waiting:
            g = Table.grid(padding=(0, 1))
            g.add_row(self._spinner, Text("watching", style=f"bold {BLUE}"))
            left = g
        else:
            left = Text(f"suijin blue · {self.target[:24]}", style=f"bold {BLUE}")
        right = Text.assemble(
            (_fmt_n(self.requests), "cyan"),
            (" req", "dim"),
            (" | ", "dim"),
            (_fmt_n(self.detected), RED),
            (" threats", "dim"),
            (" | ", "dim"),
            (_fmt_n(self.blocked), GREEN),
            (" blocked", "dim"),
            (" | ", "dim"),
            (_fmt_n(self.deceived), GOLD),
            (" deceived", "dim"),
            (" | ", "dim"),
            (up_str, "dim"),
        )
        t = Table.grid(expand=True, padding=(0, 1))
        row = [left, Text(), right]
        t.add_row(*row)
        t.columns[1].ratio = 1
        return t

    def start(self) -> None:
        if self._live is None:
            # A Live region on a non-terminal stream emits control codes to
            # stdout forever (CI kernel quiet-boot tests capture stdout
            # globally) — headless callers get the strip only on demand.
            import io

            if self._raw.is_terminal or isinstance(getattr(self._raw, "file", None), io.StringIO):
                self._live = Live(self._strip(), console=self._raw, refresh_per_second=60)
                self._live.start()
            else:
                self._live = None
                self._headless = True

    def stop(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.stop()
            self._live = None

    def tick(self) -> None:
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._live.update(self._strip())

    def waiting(self, on: bool) -> None:
        self._waiting = bool(on)
        self.tick()

    # ── event blocks (one request crossing = one block) ────────────────

    def _section(self, renderable) -> None:
        if self._sections:
            self._print(Rule(style=BORDER, align="left"))
        self._sections += 1
        self._print(renderable)

    def _print(self, r) -> None:
        with contextlib.suppress(Exception):
            self._out.print(r)
        self.tick()

    def begin_event(self, method: str, path: str, ip: str) -> None:
        """Open a request block; closes when verdict lands."""
        self._close_event()
        self.requests += 1
        self._open_event = {"method": method, "path": path, "ip": ip}
        self._sections = 0
        title = f" {method} {path[:48]} — {ip} "
        self._print(Rule(title=title, style=BORDER, align="left"))

    def _close_event(self) -> None:
        if self._open_event and self._sections:
            self._print(Rule(style=BORDER, align="left"))
        self._open_event = None
        self._sections = 0

    def verdict(self, level: str, reason: str) -> None:
        style = {"normal": "dim", "anomalous": GOLD, "investigated": RED}.get(level, "white")
        self._section(Text(f"{level.upper()}  {reason[:140]}", style=style))
        if level == "investigated":
            self.detected += 1
        self._close_event()
        self.waiting(True)

    def action(self, action: str, detail: str = "") -> None:
        color = {"BLOCK": RED, "TARPIT": GOLD, "DECEIVE": GOLD, "PATCH": GREEN}.get(action.upper(), BLUE)
        body = f"{action}" + (f"\n{detail[:400]}" if detail else "")
        self._section(Panel(body, title="action", title_align="left", border_style=color, padding=(0, 1)))
        a = action.upper()
        if "BLOCK" in a:
            self.blocked += 1
        elif "DECEIVE" in a:
            self.deceived += 1
        elif "TARPIT" in a:
            self.tarpitted += 1
        self.tick()

    def command(self, cmd: str) -> None:
        with contextlib.suppress(Exception):
            r = (
                Syntax(str(cmd)[:600], "bash", theme="monokai", word_wrap=True, background_color="default")
                if self._raw.is_terminal
                else Text(str(cmd)[:600], style="dim")
            )
            self._section(r)
        self.tick()

    def watcher(self, report: str) -> None:
        if report:
            self._section(Text(report[:400], style="bold magenta"))

    def thinking(self, thought: str) -> None:
        if thought:
            self._section(Markdown(str(thought)[:1200]))

    def note(self, text: str, style: str = "dim") -> None:
        if text:
            self._section(Text(str(text)[:400], style=style))

    def banner(self, text: str, style: str = BLUE) -> None:
        self._close_event()
        self._print(Panel(str(text)[:800], title=" blue session ", title_align="left", border_style=style))
