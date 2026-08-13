"""Alert panel — critical threat popups."""
from __future__ import annotations
from rich.console import Console
from rich.panel import Panel

console = Console()

def show_alert(attacker_id: str, endpoint: str, attack_type: str, score: int, confidence: float):
    console.print(Panel(f"[bold red]CRITICAL THREAT[/bold red]\n{attacker_id} -> {endpoint}\n{attack_type} | Score: {score}/10 | Confidence: {confidence:.0%}", border_style="red"))

def show_deception(attacker_id: str, tactic: str):
    console.print(f"  [bold magenta]DECEPTION:[/bold magenta] {tactic} applied to {attacker_id}")
