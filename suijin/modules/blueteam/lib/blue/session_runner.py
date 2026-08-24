"""Blue session runner — the live console experience (BF3).

The operator sees a real-time SOC console:
  - events scroll as requests cross (verdict -> action blocks)
  - a pinned strip at the bottom (req · threats · blocked · deceived · cost)
  - an INPUT BOX that is always active — type /block IP, /state, /tarpits
    or free-form shell commands WHILE the session processes traffic

Architecture: the Live region is the strip (one row, bottom); events
print above it; a daemon thread reads stdin and dispatches commands
through the blue tool registry — the RunBox pattern red proved, in
blue doctrine.
"""

from __future__ import annotations

import threading
import time

from rich.console import Console
from rich.panel import Panel

from suijin.modules.blueteam.lib.blue.console_ui import BLUE, BlueConsoleUI

HINT = "[dim]live commands — /state /block <ip> /unblock <ip> /tarpits /canaries /report /shell <cmd> /quit[/dim]"


class BlueCommandBox:
    """Daemon stdin reader — the always-active input box."""

    def __init__(self, ui: BlueConsoleUI, console: Console):
        self.ui = ui
        self.console = console
        self._handlers: dict[str, callable] = {}
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._history: list[str] = []
        self._register_defaults()

    def start(self) -> "BlueCommandBox":
        if self._reader and self._reader.is_alive():
            return self
        self._reader = threading.Thread(target=self._read_loop, name="blue-cmdbox", daemon=True)
        self._reader.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def register(self, name: str, fn) -> None:
        self._handlers[name.lstrip("/").lower()] = fn

    def _read_loop(self) -> None:
        import sys

        try:
            for line in sys.stdin:
                if self._stop.is_set():
                    break
                self.dispatch(line.strip())
        except Exception:
            return  # stdin closed (piped/headless) — box goes quiet

    def dispatch(self, line: str) -> None:
        if not line:
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
