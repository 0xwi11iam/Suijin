"""Blue team dashboard — main TUI."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

console = Console()

def render_dashboard(session, recent_threats: list, metrics: dict):
    console.print(Panel.fit("[bold #58a6ff]BLUE TEAM — Active Defense[/bold #58a6ff]", border_style="#58a6ff"))
    console.print(f"  Endpoints Watched: {session.endpoints_discovered}  |  Requests: {session.total_requests_processed}")
    console.print(f"  Threats Blocked: {session.threats_blocked}  |  Deceived: {session.threats_deceived}  |  Hotfixes: {session.hotfixes_deployed}")
    console.print(f"  Active Watchers: {session.active_watchers}  |  Cost: ${session.total_cost_usd:.4f}")
    console.print("─" * 60)
    for threat in recent_threats[:5]:
        level_color = {"critical": "bold red", "suspicious": "yellow", "noise": "dim"}.get(threat.get("level",""), "white")
        console.print(f"  [{level_color}]{threat.get('level','?').upper()}[/{level_color}] {threat.get('ip','?')} -> {threat.get('endpoint','?')}")
        console.print(f"    {threat.get('type','?')} (score {threat.get('score','?')}/10) — {threat.get('action','?')}")
    console.print("─" * 60)
