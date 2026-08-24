"""Blue session runner — the live console experience (BF3).

The operator sees a real-time SOC console:
  - events scroll as requests cross (verdict -> action blocks)
  - a pinned 3-row strip (watching · stats+clock · input box)
  - an INPUT BOX that is always active — type /block IP, /state, /tarpits
    or free-form shell commands WHILE the session processes traffic

BF3.5 — the input box is REAL: on a TTY the reader switches stdin to
cbreak and captures keystrokes (type/backspace/Enter live in the strip's
box row); piped/headless falls back to the line loop. Ctrl+C keeps its
signal semantics (cbreak leaves ISIG on — the pause console still gets
it). Architecture: the Live region is the strip; events print above it;
a daemon thread owns stdin — the RunBox pattern red proved, blue doctrine.
"""

from __future__ import annotations

import contextlib
import select
import sys
import threading
import time

from rich.console import Console
from rich.panel import Panel

from suijin.modules.blueteam.lib.blue.console_ui import BLUE, BlueConsoleUI

HINT = "[dim]live commands — /state /block <ip> /unblock <ip> /tarpits /canaries /report /shell <cmd> /quit[/dim]"


class _KeystrokeReader:
    """cbreak-mode stdin keystroke pump (TTY only).

    Owns termios state; restores on stop and at interpreter exit (a
    crashed session must not leave the operator's shell raw).
    """

    def __init__(self, on_line: callable, on_edit: callable, on_intr: callable | None = None):
        self._on_line = on_line  # complete line (Enter)
        self._on_edit = on_edit  # buffer changed (strip box render)
        self._on_intr = on_intr  # Ctrl+C handled HERE (cbreak keeps ISIG off
        # only if we raw-mode it; we cbreak WITH ISIG so ^C signals normally —
        # on_intr is the fallback for platforms where ISIG is off)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._termios_saved = False

    # ── keystroke editing (pure, unit-testable) ────────────────────────

    @staticmethod
    def apply_key(buf: str, key: str) -> tuple[str, str | None]:
        """One keystroke -> (new buffer, action). Actions: 'line', None.
        Pure function — the editor contract the tests pin."""
        if key in ("\r", "\n"):
            return "", "line"
        if key in ("\x7f", "\x08"):  # backspace / ctrl-h
            return buf[:-1], None
        if key == "\x15":  # ctrl-u — clear line
            return "", None
        if key == "\x03":  # ctrl-c — pass through as interrupt marker
            return buf, "intr"
        if key.startswith("\x1b"):  # arrows/pgup/etc — ignore
            return buf, None
        if key.isprintable():
            return (buf + key)[:120], None
        return buf, None  # any other control char: ignore

    # ── TTY lifecycle ──────────────────────────────────────────────────

    def start(self) -> bool:
        """Enter cbreak + pump. False when stdin isn't a TTY (caller falls
        back to the line loop)."""
        import termios
        import tty

        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            return False
        try:
            attrs = termios.tcgetattr(fd)
        except termios.error:
            return False  # not a terminal (pipe/test/docker) — fall back
        import atexit

        try:
            tty.setcbreak(fd)  # ISIG stays ON: ^C still signals the process
        except termios.error:
            return False
        self._termios_saved = True
        self._saved_attrs = attrs
        atexit.register(self._restore)
        self._thread = threading.Thread(target=self._pump, name="blue-keys", daemon=True)
        self._thread.start()
        return True

    def _restore(self) -> None:
        import termios

        if self._termios_saved:
            with contextlib.suppress(Exception):
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved_attrs)
            self._termios_saved = False

    def stop(self) -> None:
        self._stop.set()
        self._restore()

    def _pump(self) -> None:
        """Read stdin one byte at a time via select (never blocks exit —
        the daemon thread dies with the process; termios is restored by
        atexit regardless). Enter flushes the line; ^C is a normal signal
        (cbreak keeps ISIG) — apply_key handles the editing keys only."""
        fd = sys.stdin.fileno()
        buf = ""
        while not self._stop.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not r:
                continue
            try:
                b = sys.stdin.read(1)
            except (OSError, ValueError):
                return
            if not b:
                return  # EOF
            if b in ("\r", "\n"):
                line, buf = buf, ""
                self._on_edit(buf)
                if line:
                    with contextlib.suppress(Exception):
                        self._on_line(line)
                continue
            buf, action = self.apply_key(buf, b)
            if action == "intr" and self._on_intr:
                self._on_intr()
            else:
                self._on_edit(buf)


class BlueCommandBox:
    """Daemon stdin reader — the always-active input box."""

    def __init__(self, ui: BlueConsoleUI, console: Console):
        self.ui = ui
        self.console = console
        self._handlers: dict[str, callable] = {}
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._history: list[str] = []
        self._keys: _KeystrokeReader | None = None
        self._register_defaults()

    def start(self) -> "BlueCommandBox":
        if self._reader and self._reader.is_alive():
            return self
        self._keys = _KeystrokeReader(
            on_line=self.dispatch,
            on_edit=self.ui.set_input,
            on_intr=None,  # ^C keeps signal semantics (pause console)
        )
        if self._keys.start():
            return self  # keystroke mode live — box is real
        self._keys = None
        self._reader = threading.Thread(target=self._read_loop, name="blue-cmdbox", daemon=True)
        self._reader.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._keys is not None:
            self._keys.stop()
            self._keys = None
        self.ui.set_input(None)

    def register(self, name: str, fn) -> None:
        self._handlers[name.lstrip("/").lower()] = fn

    def _read_loop(self) -> None:
        try:
            for line in sys.stdin:
                if self._stop.is_set():
                    break
                self.dispatch(line.strip())
        except Exception:
            return  # stdin closed (piped/headless) — box goes quiet

    def dispatch(self, line: str) -> None:
        if not line:
            self.ui.set_input(None)
            return
        self._history.append(line)
        self._history = self._history[-100:]
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            handler = self._handlers.get(cmd.lower())
            if handler:
                try:
                    handler(rest.strip())
                except Exception as e:  # noqa: BLE001 — commands never break the session
                    self.ui.note(f"/{cmd} failed: {e}", "red")
            else:
                self.ui.note(
                    f"unknown /{cmd} — /state /block /unblock /tarpits /canaries /report /shell /quit", "yellow"
                )
        else:
            # free-form = shell command (the operator's freedom)
            self._handlers.get("shell", lambda _: None)(line)
        self.ui.set_input(None)

    def _register_defaults(self) -> None:
        from suijin.modules.blueteam.lib.blue import enforcement
        from suijin.modules.blueteam.lib.blue.tools import route_blue_tool

        def state(_):
            lines = [
                f"requests: {self.ui.requests} | detected: {self.ui.detected} | blocked: {self.ui.blocked} | deceived: {self.ui.deceived}",
                f"blocked IPs: {', '.join(enforcement.blocked_ips()) or '(none)'}",
                f"canary hits: {len(enforcement.canary_hits())}",
            ]
            self.ui.note("\n".join(lines), "cyan")

        def block(args):
            ip = args.split()[0] if args else ""
            if ip:
                self.ui.note(route_blue_tool("blue_block", {"ip": ip, "reason": "operator"}), "green")
            else:
                self.ui.note("usage: /block <ip>", "yellow")

        def unblock(args):
            ip = args.split()[0] if args else ""
            if ip:
                self.ui.note(route_blue_tool("blue_unblock", {"ip": ip}), "green")
            else:
                self.ui.note("usage: /unblock <ip>", "yellow")

        def tarpits(_):
            from suijin.modules.blueteam.lib.blue.defense import tarpit

            self.ui.note(f"tarpit file: {tarpit.delay_for.__module__} — check /tmp/blue_tarpit.json", "dim")

        def canaries(_):
            self.ui.note(route_blue_tool("blue_canary_hits", {}), "cyan")

        def report(_):
            up = int(time.monotonic() - self.ui.started)
            self.ui.note(
                f"session report — {self.ui.requests} req | {self.ui.detected} threats | "
                f"{self.ui.blocked} blocked | {self.ui.deceived} deceived | {up}s uptime",
                "cyan",
            )

        def shell(cmd):
            if cmd:
                self.ui.note(f"$ {cmd}", "dim")
                self.ui.note(route_blue_tool("blue_shell", {"cmd": cmd}), "white")

        def rotate(_):
            self.ui.note(route_blue_tool("blue_force_rotate", {"reason": "operator command"}), "green")

        for name, fn in {
            "state": state,
            "block": block,
            "unblock": unblock,
            "tarpits": tarpits,
            "canaries": canaries,
            "report": report,
            "shell": shell,
            "rotate": rotate,
        }.items():
            self.register(name, fn)


def start_session(console: Console, target: str, feed=None) -> tuple[BlueConsoleUI, BlueCommandBox]:
    """Boot the live console + command box. Returns (ui, box).

    Wire `feed.ui = ui` so traffic events render as blocks; the strip
    and input stay live for the session's lifetime."""
    ui = BlueConsoleUI(console, target=target)
    ui.start()
    if feed is not None:
        feed.ui = ui
    box = BlueCommandBox(ui, console).start()
    console.print(
        Panel(
            f"[bold {BLUE}]BLUE SESSION[/bold {BLUE}] — {target}\n{HINT}",
            title=" suijin blue ",
            title_align="left",
            border_style=BLUE,
        )
    )
    ui.waiting(True)
    return ui, box
