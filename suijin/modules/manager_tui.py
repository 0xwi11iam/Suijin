"""Suijin Module Manager — a Rich TUI (no external TUI framework).

A tier-grouped module table + a detail panel over the management API.
Every action routes through suijin.modules.manager (the same API the CLI
verbs use) — this module is pure rendering, and every action is
failure-isolated (a bad module dir shows a line, never a crash screen).

  ↑/↓ or j/k  move      space  enable/disable
  i  info   d  deps   p  permissions   b  boot report
  a  install (path)    x  uninstall     q  quit
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from suijin.modules import manager as mgmt

TIER_STYLES = {"core": "bold red", "recommended": "bold yellow", "installed": "bold cyan"}
_TIER_ORDER = {"core": 0, "recommended": 1, "installed": 2}


def _key_stream():
    """Single-key reader over the TTY (j/k + arrows), else None (line mode).
    The terminal is always restored."""
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover
        return None
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return None
    except Exception:  # noqa: BLE001
        return None

    def _read() -> str:
        try:
            old = termios.tcgetattr(fd)
        except Exception:  # noqa: BLE001
            return ""
        try:
            tty.setcbreak(fd)  # ISIG stays ON: Ctrl+C still signals
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(sys.stdin.read(2), "esc")
            return {"\r": "enter", "\n": "enter"}.get(ch, ch)
        except Exception:  # noqa: BLE001
            return ""
        finally:
            with contextlib.suppress(Exception):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)

    return _read


class ModuleManager:
    """The Rich editor. `.run()` is the entry (the CLI verb calls it)."""

    def __init__(self, module_roots: list[Path] | None = None, console: Console | None = None) -> None:
        self._roots = module_roots
        self._console = console or Console()
        self._entries: list[dict] = []
        self._cursor = 0
        self._last_detail: str = ""

    # -- data ------------------------------------------------------------
    def refresh(self) -> list[dict]:
        """Reload the module list (tier-grouped). Never raises."""
        with contextlib.suppress(Exception):
            self._entries = sorted(
                mgmt.list_modules(self._roots),
                key=lambda x: (_TIER_ORDER.get(x.get("tier"), 9), str(x.get("id"))),
            )
        return self._entries

    def _selected_id(self) -> str | None:
        if 0 <= self._cursor < len(self._entries):
            return str(self._entries[self._cursor].get("id"))
        return None

    def _show(self, text: str) -> str:
        self._last_detail = str(text or "")
        return self._last_detail

    # -- rendering -------------------------------------------------------
    def _table(self) -> Table:
        table = Table(
            title="SUIJIN — Module Manager",
            title_style="bold white",
            header_style="dim",
            expand=False,
            pad_edge=False,
        )
        table.add_column("Tier", no_wrap=True)
        table.add_column("Module", style="cyan", no_wrap=True)
        table.add_column("Version", no_wrap=True)
        table.add_column("On", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        for idx, e in enumerate(self._entries):
            tier = str(e.get("tier", ""))
            name = str(e.get("id", ""))
            if idx == self._cursor:
                name = f"[reverse] {name} [/reverse]"
            on = "●" if e.get("enabled") else "○"
            table.add_row(
                f"[{TIER_STYLES.get(tier, 'white')}]{tier}[/]",
                name,
                str(e.get("version", "")),
                on,
                "ok" if e.get("enabled") else "disabled",
            )
        return table

    def render(self) -> None:
        self._console.print(self._table())
        detail = self._last_detail or "move with ↑/↓ · i info · b boot report · q quit"
        self._console.print(Panel(detail, title=" detail ", border_style="#30363d"))
        self._console.print(
            "  [dim]↑/↓ or j/k move · space enable/disable · i info · d deps · a install · x uninstall "
            "· p perms · b boot report · q quit[/dim]"
        )

    # -- actions (pure: they return the detail text) ---------------------
    def action_info(self, mid: str | None = None) -> str:
        mid = mid or self._selected_id()
        if not mid:
            return self._show("no module selected")
        try:
            info = mgmt.module_info(mid, self._roots)
        except (mgmt.InstallError, OSError) as e:  # module dirs/permissions
            return self._show(str(e))
        deps = ", ".join(info.get("requires") or []) or "—"
        perms = ", ".join(info.get("permissions") or []) or "—"
        return self._show(
            f"{mid} v{info.get('version')}\n"
            f"tier: {info.get('tier')}   enabled: {info.get('enabled')}\n"
            f"requires: {deps}\n"
            f"provides: {', '.join(info.get('provides') or []) or '—'}\n"
            f"permissions: {perms}\n"
            f"source: {info.get('source')}"
        )

    def action_toggle(self) -> str:
        mid = self._selected_id()
        if not mid:
            return self._show("no module selected")
        new = not mgmt.is_enabled(mid)
        if mgmt.set_enabled(mid, new):
            self._show(f"{mid}: {'enabled' if new else 'disabled'} (next boot)")
            self.refresh()
        return self._last_detail

    def action_deps(self, mid: str | None = None) -> str:
        mid = mid or self._selected_id()
        if not mid:
            return self._show("no module selected")
        with contextlib.suppress(Exception):
            info = mgmt.module_info(mid, self._roots)
            report = mgmt._all_units(self._roots)
            lines = [f"dependencies of {mid}:"]
            for dep in info.get("requires") or []:
                suffix = f" (skipped: {report.skipped[dep]})" if dep in report.skipped else ""
                lines.append(f"  · {dep}{suffix}")
            dependents = [u.id for u in report.units.values() if mid in u.requires]
            if dependents:
                lines.append("required by: " + ", ".join(sorted(dependents)))
            return self._show("\n".join(lines))
        return self._show("could not read the dependency report")

    def action_perms(self) -> str:
        mid = self._selected_id()
        if not mid:
            return self._show("no module selected")
        with contextlib.suppress(Exception):
            info = mgmt.module_info(mid, self._roots)
            perms = "\n".join(f"  • {p}" for p in info.get("permissions") or []) or "  (none declared)"
            return self._show(f"permissions declared by {mid}:\n{perms}")
        return self._show("could not read permissions")

    def action_boot(self) -> str:
        with contextlib.suppress(Exception):
            report = mgmt._all_units(self._roots)
            lines = [report.summary()]
            if report.skipped:
                lines.append("skipped:")
                lines += [f"  {k}: {v}" for k, v in report.skipped.items()]
            if report.quarantined:
                lines.append("quarantined:")
                lines += [f"  {k}: {v}" for k, v in report.quarantined.items()]
            return self._show("\n".join(lines))
        return self._show("could not build the boot report")

    def action_install(self, path: str | None = None) -> str:
        if path is None:
            try:
                path = Prompt.ask("install module from path", console=self._console, default="")
            except (EOFError, KeyboardInterrupt):
                return self._show("install cancelled")
        if not path:
            return self._show("install cancelled")
        try:
            out = mgmt.install(path)
        except (mgmt.InstallError, OSError) as e:
            return self._show(f"install failed: {e}")
        self.refresh()
        return self._show(str(out))

    def action_uninstall(self) -> str:
        mid = self._selected_id()
        if not mid:
            return self._show("no module selected")
        try:
            if mgmt.uninstall(mid):
                self._show(f"{mid}: uninstalled")
                self.refresh()
            else:
                self._show(f"{mid}: not uninstalled")
        except (mgmt.InstallError, OSError) as e:
            self._show(f"uninstall refused: {e}")
        return self._last_detail

    # -- the loop --------------------------------------------------------
    def _step(self, key: str) -> bool:
        """One keystroke. False = quit."""
        if key in ("q", "Q", "\x03", "esc"):
            return False
        if key in ("up", "k"):
            self._cursor = (self._cursor - 1) % max(1, len(self._entries))
            self.action_info()
        elif key in ("down", "j"):
            self._cursor = (self._cursor + 1) % max(1, len(self._entries))
            self.action_info()
        elif key in (" ", "space"):
            self.action_toggle()
        elif key == "i":
            self.action_info()
        elif key == "d":
            self.action_deps()
        elif key == "p":
            self.action_perms()
        elif key == "b":
            self.action_boot()
        elif key == "a":
            self.action_install()
        elif key == "x":
            self.action_uninstall()
        return True

    def _step_line(self, line: str) -> bool:
        """Line mode (no TTY): a command word runs an action. False = quit."""
        cmd = (line or "").strip().lower()
        if cmd in ("q", "quit", ""):
            return False
        if cmd in ("a", "install"):
            rest = (line or "").strip()[1:].strip()
            self.action_install(rest or None)
        else:
            self._step(cmd)
        return True

    def run(self) -> int:
        self.refresh()
        read_key = _key_stream()
        try:
            while self._entries:
                self.render()
                if read_key is None:
                    try:
                        line = self._console.input("[dim]command (b boot report, q quit): [/] ")
                    except (EOFError, KeyboardInterrupt):
                        return 0
                    if not self._step_line(line):
                        return 0
                    continue
                if not self._step(read_key()):
                    return 0
        except KeyboardInterrupt:  # Ctrl+C — quiet exit
            return 0
        except Exception as e:  # noqa: BLE001 — a manager crash is a line, not a traceback
            self._console.print(f"[bold red]module manager error[/] — {type(e).__name__}: {e}")
            return 1
        return 0


def main() -> int:
    return ModuleManager().run()


if __name__ == "__main__":
    raise SystemExit(main())
