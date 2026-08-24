"""Blue console UI — the SOC operator's live view (BF3).

Red-parity visual language, blue doctrine: event panels (request ->
verdict -> action), thinking/said sections, a pinned live strip
(req · threats · blocked · deceived · watchers · cost), a pause
console (/block /unblock /state /tarpits /report /health /quit),
crash logs, and termination banners. Rich only, no emojis. Every
public render method is guarded — a render bug must never kill a
defense session.

BF3.5 — the clean console (operator contract):
  - the strip is THREE pinned rows, all redrawn in place (zero scroll):
    watching row (transient) · stats+clock · the input box
  - benign requests are SILENT (the req counter ticks, nothing prints)
  - baseline training is strip-only (a `baseline N/M` stat, no lines)
  - the clock ticks every second, bright
"""

from __future__ import annotations

import contextlib
import threading
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

INPUT_HINT = "/block <ip> · /state · /shell <cmd> — type anytime"


def _fmt_n(n) -> str:
    return f"{int(n or 0)}"


def _fmt_clock(s: int) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:d}:{sec:02d}"


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
        self._refresh_thread: threading.Thread | None = None
        self._refresh_stop = threading.Event()
        # BF3.5 transient/pinned rows
        self._watching: str | None = None  # "GET /path — ip" while a request processes
        self._baseline: tuple[int, int] | None = None  # (done, total) while training
        self._input_buf: str | None = None  # None = hint; str = live typing (cursor ▌)

    # ── strip ───────────────────────────────────────────────────────────

    def _watching_row(self) -> Text:
        row = Text()
        row.append(self._spinner.render(time.monotonic()))  # carries its own style
        if self._watching:
            row.append(" ")
            row.append(self._watching[:70], style=f"bold {BLUE}")
        elif self._waiting:
            row.append(" watching", style=f"bold {BLUE}")
        else:
            row.append(" idle", style="dim")
        return row

    def _stats_row(self) -> tuple[Text, Text]:
        up = int(time.monotonic() - self.started)
        left = Text(f"suijin blue · {self.target[:24]}", style=f"bold {BLUE}")
        parts = [
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
        ]
        if self._baseline is not None:
            done, total = self._baseline
            parts += [(" | ", "dim"), (f"baseline {min(done, total)}/{total}", GOLD)]
        parts += [(" | ", "dim"), (_fmt_clock(up), "bright_cyan")]
        return left, Text.assemble(*parts)

    def _input_row(self) -> Text:
        if self._input_buf is not None:
            return Text.assemble(("» ", f"bold {BLUE}"), (self._input_buf[:80], "white"), ("▌", BLUE))
        return Text.assemble(("» ", f"dim {BLUE}"), (INPUT_HINT, "dim"))

    def _strip(self) -> Table:
        left, right = self._stats_row()
        t = Table.grid(expand=True, padding=(0, 1))
        t.add_row(self._watching_row(), Text(), Text())
        t.add_row(left, Text(), right)
        t.add_row(self._input_row(), Text(), Text())
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
                # the heartbeat: rebuild the strip once a second so the
                # uptime counter ticks and the spinner NEVER freezes between
                # requests (a static renderable goes stale; the Live region
                # only redraws what changed)
                self._refresh_stop.clear()
                self._refresh_thread = threading.Thread(target=self._heartbeat, name="blue-strip", daemon=True)
                self._refresh_thread.start()
            else:
                self._live = None
                self._headless = True

    def _heartbeat(self) -> None:
        while not self._refresh_stop.wait(1.0):
            self.tick()

    def stop(self) -> None:
        self._refresh_stop.set()
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

    # ── transient strip rows (BF3.5) ────────────────────────────────────

    def set_watching(self, label: str) -> None:
        """Show `GET /path — ip` in the strip while a request processes —
        prints NOTHING (it vanishes the moment the verdict lands)."""
        self._watching = str(label)[:70] if label else None
        self.tick()

    def clear_watching(self) -> None:
        self._watching = None
        self.tick()

    def baseline_stat(self, done: int, total: int) -> None:
        """Baseline training: a `baseline N/M` stat INSIDE the strip —
        zero console lines (per-request banners were pure spam)."""
        self.requests += 1
        self._baseline = (int(done), int(total))
        self.tick()

    def baseline_done(self) -> None:
        self._baseline = None
        self.tick()

    def set_input(self, buf: str | None) -> None:
        """The input box row: None shows the command hint; a str shows the
        operator's live typing with a block cursor."""
        self._input_buf = None if buf is None else str(buf)[:80]
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
        """A request starts crossing: occupy the watching row ONLY — no
        Rule, no print. The block (if any) materializes at verdict time."""
        self._close_event()
        self.requests += 1
        self._open_event = {"method": method, "path": path, "ip": ip}
        self._sections = 0
        self.set_watching(f"{method} {path[:48]} — {ip}")

    def _close_event(self) -> None:
        if self._open_event and self._sections:
            self._print(Rule(style=BORDER, align="left"))
        self._open_event = None
        self._sections = 0

    def _materialize(self, method: str, path: str, ip: str) -> None:
        """Print the block header LATE (verdict time): benign traffic never
        scrolled; detections render with full context now."""
        self._print(Rule(title=f" {method} {path[:48]} — {ip} ", style=BORDER, align="left"))
        self._sections = 0

    def verdict(self, level: str, reason: str) -> None:
        style = {"normal": "dim", "anomalous": GOLD, "investigated": RED}.get(level, "white")
        # the watching row's lifecycle ends here — auto-delete
        ev = self._open_event or {}
        if level == "investigated":
            self._materialize(ev.get("method", "?"), ev.get("path", "?"), ev.get("ip", "?"))
            self._section(Text(f"{level.upper()}  {reason[:140]}", style=style))
            self.detected += 1
            self._close_event()
        elif level == "anomalous":
            line = f"{ev.get('method', '?')} {ev.get('path', '?')[:40]} — {ev.get('ip', '?')} · {reason[:80]}"
            self._print(Text(f"~ {line}", style=GOLD))
        # normal: silent — the req counter already ticked
        self._open_event = None
        self._watching = None
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

    # ── headless strip snapshot (no Live needed) ────────────────────────

    def render_strip_text(self, width: int = 100) -> str:
        """One-line text snapshot of the strip (tests + headless /state)."""
        import io

        c = Console(file=io.StringIO(), width=width, force_terminal=False)
        with contextlib.suppress(Exception):
            c.print(self._strip())
        return c.file.getvalue()
