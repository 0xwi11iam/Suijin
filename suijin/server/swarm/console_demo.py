"""suijin swarm console — demo with fake data to see the UI design.

Real version is console_rich.py. This file is for the operator to see
the design without needing a live swarm.
"""

import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.console import Group

console = Console()
POLL_S = 3.0


def run_demo():
    """Demo with fake data — the UI design, no live data needed."""
    agents = [
        {"id": "agent-1321-54a4", "lane": "recon: 47 subdomains, 2 API routes", "tier": "M3", "hb": 3, "boxed": False},
        {"id": "agent-1320-d840-sbx", "lane": "exploit: testing /api/v2 auth bypass", "tier": "M2", "hb": 5, "boxed": True},
        {"id": "agent-1320-2f7f-sbx", "lane": "recon: subfinder sweep", "tier": "M3", "hb": 12, "boxed": True},
        {"id": "agent-1321-9f5e", "lane": "coder: JWT decoder from response tokens", "tier": "M2", "hb": 8, "boxed": False},
    ]
    intents = {"open": 3, "running": 4, "done": 2, "blocked": 0, "exhausted": 1}
    findings = [
        {"severity": "high", "status": "reported", "title": "role param auth bypass /api/v2/users"},
        {"severity": "medium", "status": "verified", "title": "JWT alg confusion on /auth/token"},
        {"severity": "low", "status": "false_positive", "title": "directory listing /backup"},
    ]

    t = Table(title="agents", show_header=False, expand=True)
    t.add_column("box")
    t.add_column("id", style="cyan")
    t.add_column("lane")
    t.add_column("hb", justify="right")
    for a in agents:
        box_tag = "[box]" if a["boxed"] else ""
        t.add_row(box_tag, a["id"], a["lane"][:28], f"{a['hb']}s")

    ft = Table(title="findings — honest board", show_header=True, expand=True)
    ft.add_column("sev", style="bold")
    ft.add_column("status")
    ft.add_column("title")
    for f in findings:
        ft.add_row(f["severity"], f["status"], f["title"][:50])

    it = Table(title="intents", show_header=True)
    it.add_column("state")
    it.add_column("count", justify="right")
    for k, v in sorted(intents.items()):
        it.add_row(k, str(v))

    return Panel.fit(
        Table.grid(expand=True).add_row(
            Panel(t, title="agents"),
            Panel(Group(ft, it), title="intents + findings"),
        ),
        title="[bold]suijin swarm console — demo[/]",
        border_style="cyan",
    )


def run():
    console.print(Panel(
        "[dim]demo mode — fake data to see the design. the live version reads swarm.db[/dim]",
        style="dim",
    ))
    run_demo()


console = Console()


def demo_loop():
    console.print("[bold]suijin swarm console — demo mode[/bold]\n")
    console.print(run_demo())
    console.print("\n[dim]Ctrl+C to exit[/dim]")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[dim]exited[/dim]")


if __name__ == "__main__":
    demo_loop()
