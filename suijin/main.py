import contextlib
import os
import subprocess
import sys
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

from suijin.modules.console.lib import tui_settings
from suijin.modules.redteam.lib.redteamer import main as redteamer_main

console = Console()

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "console", "lib", "cli.py")

OPERATOR_TOOLS = [
    ("EXPLOIT now — instant, uses all known intel", ["exploit"]),  # target prompted below
    ("Resume a saved engagement (.sje)", ["load-prompt"]),  # path prompted below
    ("Scope editor (Burp-style TUI)", ["scope"]),
    ("Approvals console (HITL)", ["approvals", "list"]),
    ("Battle — red vs blue on the lab", ["battle"]),
    ("Engagement debrief", ["debrief"]),
    ("Replay an engagement", ["replay"]),
    ("Target dossier", None),  # prompts for target
    ("Unified timeline", ["timeline"]),
    ("Lab fleet + campaign", ["labs", "list"]),
    ("Knowledge base status", ["pull", "kb", "--status"]),
    ("Workspace cleaner", ["clean"]),
    ("Notifications test", ["notify", "test"]),
    ("Provider health probe", ["providers"]),
    ("PANIC — stop everything", ["panic"]),
]


def _run_cli(args):
    subprocess.run([sys.executable, CLI, *args])


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
        if args == ["exploit"]:  # /exploit needs a target
            try:
                target = input("  target (IP / hostname / URL): ").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            if target:
                _run_cli(["exploit", target])
        elif args == ["load-prompt"]:  # resume a saved engagement
            try:
                path = input("  .sje path (or name from outputs/exports/): ").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            if path:
                _run_cli(["load", path])
        elif args is None:  # dossier needs a target
            try:
                target = input("  target (IP / hostname / URL): ").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            if target:
                _run_cli(["dossier", target])
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

    # The Textual Shell: dragon left pane, clickable pages. Falls back to
    # the legacy plain flow on non-TTY / narrow / NO_COLOR (CI, containers).
    try:
        import shutil as _sh

        if sys.stdout.isatty() and not os.environ.get("NO_COLOR") and _sh.get_terminal_size().columns >= 100:
            from suijin.modules.console.lib.shell import run_shell

            result = run_shell()
            if result is None or result.kind == "quit":
                return
            if result.kind == "red":
                from suijin.modules.platform.lib.config_loader import load_config
                from suijin.modules.redteam.lib.redteamer import run_red_team

                cfg = load_config()
                print(chr(27) + "[2J\033[H", end="")
                run_red_team(cfg, result.objective)
                return
            if result.kind == "blue":
                import asyncio
                import io as _io

                from suijin.modules.blueteam.lib import blueteamer as _bt

                print(chr(27) + "[2J\033[H", end="")
                _old_stdin = sys.stdin
                sys.stdin = _io.StringIO(f"1\n{result.path}\n{result.port}\n")
                try:
                    asyncio.run(_bt._run_async())
                finally:
                    sys.stdin = _old_stdin
                return
            if result.kind == "blue_lab":
                import asyncio
                import io as _io

                from suijin.modules.blueteam.lib import blueteamer as _bt

                _lab_choice = "1" if result.lab == "blue_target" else "2"
                print(chr(27) + "[2J\033[H", end="")
                _old_stdin = sys.stdin
                sys.stdin = _io.StringIO(f"2\n{_lab_choice}\n")
                try:
                    asyncio.run(_bt._run_async())
                finally:
                    sys.stdin = _old_stdin
                return
            return
    except Exception:
        pass  # any shell failure falls through to the legacy flow below

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
    console.print(" [dim]Press [bold #58a6ff]Enter[/] to continue...", end="")
    try:
        input()
    except KeyboardInterrupt:
        # Ctrl+C at the welcome prompt: leave quietly, no traceback
        console.print("\n[dim]cancelled[/dim]")
        return
    except EOFError:
        return

    while True:
        print(chr(27) + "[2J\033[H", end="")
        with contextlib.suppress(Exception):  # the dragon, every redraw of the selector
            from suijin.modules.platform.lib.banner import render_boot_banner

            render_boot_banner(console)
        console.print(Panel.fit("[bold white]SUIJIN[/] [dim]Mode Selector[/]", border_style="#30363d"))

        console.print("\n")

        console.print("[bold white]Select Operational Module:[/]")
        console.print("  [bold #ff5555]1.[/] [white]Red Team (Autonomous Agent)[/]")
        console.print("  [bold #58a6ff]2.[/] [white]Blue Team (Active Defense)[/]")
        console.print("  [bold yellow]3.[/] [white]Settings[/]")
        console.print("  [bold #e6b47c]4.[/] [white]Operator Tools (scope, approvals, battle, debrief, …)[/]")
        console.print("  [bold white]5.[/] [dim]Exit[/]\n")

        try:
            c = input(" ").strip()
            if c == "1":
                redteamer_main()
            elif c == "2":
                from suijin.modules.blueteam.lib.blueteamer import main as blueteam_main

                blueteam_main()
            elif c == "3":
                tui_settings.main()
            elif c == "4":
                operator_menu()
            else:
                sys.exit(0)
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
