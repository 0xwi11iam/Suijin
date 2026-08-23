import os
import subprocess
import sys
import warnings

from suijin.modules.platform.lib.config_models import CostCapWarning

warnings.filterwarnings("ignore", category=CostCapWarning)  # shown as ONE red line instead
# third-party deprecation noise (langgraph serializer advisory) — never actionable for the operator
warnings.filterwarnings("ignore", message=".*allowed_objects.*")  # any category — langchain uses its own base class

# Make sure the parent dir is on sys.path so `from suijin import …` works
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from suijin.modules.console.lib import tui_settings
from suijin.modules.redteam.lib.redteamer import main as redteamer_main

console = Console()

CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "console", "lib", "cli.py")

OPERATOR_TOOLS = [
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
        if args is None:  # dossier needs a target
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

    console.print(Panel(Text("Welcome to Suijin", style="bold #e6b47c"), border_style="#e6b47c", expand=False))
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
