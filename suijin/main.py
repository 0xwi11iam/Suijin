import contextlib
import os
import subprocess
import sys
import time
import warnings

# Make sure the parent dir is on sys.path BEFORE any `from suijin import …`
# (this file lives at /app/suijin/main.py in the container; WORKDIR is
# /app/suijin, so the package root /app is NOT on the path at startup —
# the import below detonated with ModuleNotFoundError in docker run)
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from suijin.modules.platform.lib.config_models import CostCapWarning  # noqa: E402

warnings.filterwarnings("ignore", category=CostCapWarning)  # shown as ONE red line instead
# third-party deprecation noise (langgraph serializer advisory) — never actionable for the operator
warnings.filterwarnings("ignore", message=".*allowed_objects.*")  # any category — langchain uses its own base class

from rich.console import Console
from rich.panel import Panel

from suijin.modules.redteam.lib.redteamer import main as redteamer_main

console = Console()

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "console", "lib", "cli.py")

OPERATOR_TOOLS = [
    ("Resume a saved engagement (.sje)", ["load-prompt"]),
    ("Replay an engagement", ["replay"]),
    ("Workspace cleaner", ["clean"]),
]


def _run_cli(args):
    subprocess.run([sys.executable, CLI, *args])


def _swarm_menu(console):
    """SWARM CONTROL — the advanced module. Opens the full-screen hub
    (state block + every action); plain input screens stay behind it
    for flows the hub delegates (new hunt, stop)."""
    with contextlib.suppress(Exception):
        from suijin.server.swarm.control import run_hub_cmd

        run_hub_cmd()
        return
    # fallback (no tty): the original numbered menu
    while True:
        print(chr(27) + "[2J\033[H", end="")
        console.print(Panel.fit("[bold #bd93f9]SWARM[/] [dim]Control[/]", border_style="#30363d"))
        live = False
        n_agents = 0
        with contextlib.suppress(Exception):
            from suijin.server.mesh.bus import discover_bus

            if discover_bus():
                live = True
                import sqlite3

                from suijin.server.swarm.coordinator import swarm_db_path

                db = sqlite3.connect(str(swarm_db_path()), timeout=3)
                n_agents = db.execute("SELECT COUNT(*) FROM agents WHERE last_heartbeat > ?", (time.time() - 30,)).fetchone()[0]
                db.close()
        status = f"[green]live · {n_agents} agent(s)[/green]" if live else "[dim]not running[/dim]"
        console.print(f"\n  swarm: {status}\n")
        console.print("  [bold #bd93f9]1.[/] [white]live console[/] [dim]— dashboard: agents · findings · steer · kill[/]")
        console.print("  [bold #bd93f9]2.[/] [white]new swarm[/] [dim]— objective + spawn (local or boxed agents)[/]")
        console.print("  [bold #bd93f9]3.[/] [white]status[/] [dim]— snapshot + the honest findings board[/]")
        console.print("  [bold #bd93f9]4.[/] [white]add work[/] [dim]— queue an intent for idle agents[/]")
        console.print("  [bold #bd93f9]5.[/] [white]message swarm[/] [dim]— steer every agent mid-flight[/]")
        console.print("  [bold #ff5555]6.[/] [white]stop[/] [dim]— graceful; all data survives[/]")
        console.print("  [bold white]7.[/] [dim]back[/]\n")
        try:
            c = input(" ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if c == "1":
            with contextlib.suppress(Exception):
                from suijin.server.swarm.console import run_console_cmd

                run_console_cmd()
        elif c == "2":
            try:
                objective = input("  objective: ").strip()
                if not objective:
                    continue
                where = input("  agents — (l)ocal or (b)oxed, how many [l8]: ").strip().lower() or "l8"
                n = max(1, min(32, int("".join(ch for ch in where if ch.isdigit()) or 8)))
                boxed = where.startswith("b")
                import subprocess as _sp

                _sp.run(["suijin", "swarm", "init", objective])
                _sp.run(["suijin", "swarm", "intent", objective])
                cmd = ["suijin", "swarm", "spawn"]
                cmd += ["--sandbox", str(n)] if boxed else ["-N", str(n)]
                _sp.run(cmd)
                input("\n  [dim]press Enter to open the console…[/dim]")
                with contextlib.suppress(Exception):
                    from suijin.server.swarm.console import run_console_cmd

                    run_console_cmd()
            except (KeyboardInterrupt, EOFError):
                continue
        elif c == "3":
            with contextlib.suppress(Exception):
                import subprocess as _sp

                _sp.run(["suijin", "swarm", "status"])
                input("\n  [dim]Enter to continue…[/dim]")
        elif c == "4":
            try:
                intent = input("  intent: ").strip()
                if intent:
                    import subprocess as _sp

                    _sp.run(["suijin", "swarm", "intent", intent])
            except (KeyboardInterrupt, EOFError):
                continue
        elif c == "5":
            try:
                msg = input("  message: ").strip()
                if msg:
                    import subprocess as _sp

                    _sp.run(["suijin", "swarm", "say", msg])
            except (KeyboardInterrupt, EOFError):
                continue
        elif c == "6":
            try:
                import subprocess as _sp

                _sp.run(["suijin", "swarm", "stop"])
                input("\n  [dim]Enter to continue…[/dim]")
            except (KeyboardInterrupt, EOFError):
                continue
        elif c == "7":
            return


def operator_menu():
    while True:
        print(chr(27) + "[2J\033[H", end="")
        console.print(Panel.fit("[bold white]SUIJIN[/] [dim]Operator Tools[/]", border_style="#30363d"))
        console.print("\n[bold white]Pick a tool:[/]")
        for i, (label, _args) in enumerate(OPERATOR_TOOLS, 1):
            console.print(f"  [bold #e6b47c]{i}.[/] [white]{label}[/]")
        console.print(f"  [bold white]{len(OPERATOR_TOOLS) + 1}.[/] [dim]Back[/]\n")
        try:
            c = input(" ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if not c.isdigit() or not (1 <= int(c) <= len(OPERATOR_TOOLS)):
            return
        label, args = OPERATOR_TOOLS[int(c) - 1]
        if args == ["load-prompt"]:  # resume a saved engagement
            # the CLI picker lists the ten newest bundles — no need to
            # memorize a timestamped filename
            _run_cli(["load"])
        else:
            _run_cli(args)
        try:
            input("\n[dim]Press Enter for the menu...[/] ")
        except (KeyboardInterrupt, EOFError):
            return


def main():
    # container verb support: `docker run image version` etc. — a KNOWN
    # CLI verb dispatches straight to the CLI instead of the interactive
    # TUI (unknown argv like pytest's is ignored — it's not for us)
    _argv = sys.argv[1:]
    if _argv and not _argv[0].startswith("-") and not _argv[0].endswith(".py"):
        from suijin.modules.console.lib.cli import is_known_verb

        if is_known_verb(_argv[0]):
            from suijin.modules.console.lib.cli import main as cli_main

            raise SystemExit(cli_main(_argv))

    from suijin.modules.platform.lib.runtime import init_runtime

    init_runtime()  # explicit one-time init (Phase 0 contract)
    print(chr(27) + "[2J\033[H", end="")

    # Startup availability banner — warn about missing tools before the menu.
    try:
        from suijin.modules.tools.lib.availability import startup_banner

        banner = startup_banner()
        if banner:
            console.print(f"[yellow]{banner}[/yellow]")
            print("\n")
    except Exception:
        pass

    try:  # the dragon boot banner — every TUI start
        from suijin.modules.platform.lib.banner import render_boot_banner

        render_boot_banner(console)
    except Exception:
        pass
    print("\n")

    # No keypress gate: the menu is right there. The banner and the
    # availability notice are information, not a screen to dismiss —
    # making someone press Enter before every launch was pure friction.
    while True:
        print(chr(27) + "[2J\033[H", end="")
        with contextlib.suppress(Exception):  # the dragon, every redraw of the selector
            from suijin.modules.platform.lib.banner import render_boot_banner

            render_boot_banner(console)
        # MODE-ADAPTIVE SPINE (2026-11-06 operator design): basic keeps
        # the classic 4; advanced adds SWARM CONTROL as a first-class
        # module — suijin is meant to be advanced.
        advanced = False
        with contextlib.suppress(Exception):
            from suijin.modules.platform.lib.config_loader import load_config

            advanced = str((load_config() or {}).get("operator_mode") or "basic").lower() == "advanced"

        console.print(Panel.fit("[bold white]SUIJIN[/] [dim]Mode Selector[/]", border_style="#30363d"))
        console.print("\n")
        if advanced:
            console.print("[bold white]Select Operational Module:[/] [dim]advanced[/]")
            console.print("  [bold #ff5555]1.[/] [white]Red Team[/] [dim]— one agent, one engagement, deep[/]")
            console.print("  [bold #bd93f9]2.[/] [white]Swarm Control[/] [dim]— many agents: console · spawn · steer[/]")
            console.print("  [bold yellow]3.[/] [white]Settings[/]")
            console.print("  [bold #e6b47c]4.[/] [white]Operator Tools[/] [dim](resume, replay, clean)[/]")
            console.print("  [bold white]5.[/] [dim]Exit[/]")
            # live swarm hint — one glance, no digging
            with contextlib.suppress(Exception):
                from suijin.server.mesh.bus import discover_bus

                if discover_bus():
                    console.print("\n  [green]●[/] swarm live — [cyan]2[/] opens the console")
                else:
                    console.print("\n  [dim]○ no swarm running — 2 to init one[/]")
        else:
            console.print("[bold white]Select Operational Module:[/]")
            console.print("  [bold #ff5555]1.[/] [white]Red Team (Autonomous Agent)[/]")
            console.print("  [bold yellow]2.[/] [white]Settings[/]")
            console.print("  [bold #e6b47c]3.[/] [white]Operator Tools (resume, replay, clean)[/]")
            console.print("  [bold white]4.[/] [dim]Exit[/]")
        console.print("")

        try:
            c = input(" ").strip()
            # discard typeahead BEFORE dispatching: a stale newline from
            # this input() lands in the next screen's first key-read and
            # makes it flash-exit (settings/box-menu flash, field report)
            with contextlib.suppress(Exception):
                import termios as _termios

                _termios.tcdrain(0)
                _termios.tcflush(0, _termios.TCIFLUSH)
            if c == "1":
                redteamer_main()
            elif c == "2" and advanced:
                _swarm_menu(console)
            elif c == "2":
                from suijin.modules.console.lib import settings_tui

                settings_tui.main()
            elif c == "3" and advanced:
                from suijin.modules.console.lib import settings_tui

                settings_tui.main()
            elif c == "3":
                operator_menu()
            elif (c == "4" and advanced):
                operator_menu()
            elif (c == "4" and not advanced) or (c == "5" and advanced):
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C / Ctrl+D at the menu: leave quietly. Return, don't
            # sys.exit — the console owns this frame and has nothing left
            # to unwind.
            console.print("\n[dim]cancelled[/dim]")
            return
        except Exception as e:  # noqa: BLE001 — the selector is the primary
            # entrypoint: any mode crash gets ONE red panel, never a
            # traceback wall (Ctrl+D covered; corrupt state covered)
            console.print(
                Panel.fit(
                    f"[bold red]mode failed[/bold red] {type(e).__name__}: {e}\n[dim]details: outputs/logs/[/dim]",
                    border_style="red",
                )
            )
            try:
                from suijin.modules.platform.lib.workspace import logs_dir

                d = logs_dir()
                d.mkdir(parents=True, exist_ok=True)
                with (d / "selector_crash.log").open("a") as f:
                    import traceback as _tb

                    f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + f" {e!r}\n" + _tb.format_exc() + "\n")
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
