import os
import sys

# Make sure the parent dir is on sys.path so `from medusa import …` works
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from medusa.modules.loader import load_local_module

tui_settings = load_local_module("tui_settings")

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from medusa.core.redteamer import main as redteamer_main

# Bridge init removed — standalone operation

console = Console()

def main():
    print(chr(27) + "[2J\033[H", end="")

    # Startup availability banner — warn about missing tools before the menu.
    try:
        from medusa.tools.availability import startup_banner
        banner = startup_banner()
        if banner:
            console.print(f"[yellow]{banner}[/yellow]")
            print("\n")
    except Exception:
        pass

    # Welcome Box
    welcome_text = Text("* Welcome to Medusa Latest", style="bold #e6b47c")
    console.print(Panel(welcome_text, border_style="#e6b47c", expand=False))
    print("\n")
    console.print(" [dim]Login successful.[/] Press [bold #58a6ff]Enter[/] to continue...", end="")
    input()

    print(chr(27) + "[2J\033[H", end="")
    console.print(Panel.fit("[bold white]MEDUSA[/] [dim]Mode Selector[/]", border_style="#30363d"))

    console.print("\n")

    console.print("[bold white]Select Operational Module:[/]")
    console.print("  [bold #ff5555]1.[/] [white]Red Team (Autonomous Agent + Fugu Collective Intelligence)[/]")
    console.print("  [bold #58a6ff]2.[/] [white]Blue Team (Active Defense)[/]")
    console.print("  [bold yellow]3.[/] [white]Settings[/]")
    console.print("  [bold white]4.[/] [dim]Exit[/]\n")

    try:
        c = input(" ").strip()
        if c == '1':
            redteamer_main()
        elif c == '2':
            from medusa.core.blueteamer import main as blueteam_main
            blueteam_main()
        elif c == '3':
            tui_settings.main()
            main()
        else:
            sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
