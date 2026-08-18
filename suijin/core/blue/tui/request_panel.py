"""
suijin/core/blue/tui/request_panel.py — Rich panel rendering for investigated requests.

Three-panel layout when a request triggers INVESTIGATED tier:
  TOP:    Full untruncated request details
  MIDDLE: AI reasoning — full untruncated analysis
  BOTTOM: Verdict (FLAGGED / NOT FLAGGED) + action decision panel
"""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


def render_investigated_request(result) -> None:
    """Render a full untruncated panel for an investigated request.

    Args:
        result: AIAnalysisResult from ai_engine.py
    """
    # ── TOP PANEL: Full Request Details ──
    request_table = Table(
        title=None,
        box=box.ROUNDED,
        border_style="#30363d",
        show_header=True,
        header_style="bold white",
        expand=True,
    )
    request_table.add_column("Field", style="dim cyan", width=14, no_wrap=True)
    request_table.add_column("Value", style="white")

    # Request ID and basic info
    request_table.add_row("Request #", str(result.request_id))
    request_table.add_row("Method", f"[bold {_method_color(result.method)}]{result.method}[/bold {_method_color(result.method)}]")
    request_table.add_row("Path", result.path)
    request_table.add_row("Source IP", result.ip)

    # Query parameters
    if result.query:
        qp = "\n".join(f"  {k} = {v}" for k, v in result.query.items())
        request_table.add_row("Query Params", qp)

    # Headers
    if result.headers:
        hdrs = "\n".join(f"  {k}: {v}" for k, v in result.headers.items())
        request_table.add_row("Headers", hdrs)

    # Body — FULL, untruncated
    if result.body:
        request_table.add_row("Body", result.body)
    else:
        request_table.add_row("Body", "[dim](empty)[/dim]")

    # Analysis metadata
    request_table.add_row("Analysis Time", f"{result.analysis_time_ms:.0f}ms via {result.llm_model}")

    console.print("")
    console.print(Panel(
        request_table,
        title="[bold white]REQUEST DETAILS[/bold white]",
        border_style="#58a6ff",
        padding=(1, 2),
    ))

    # ── MIDDLE PANEL: AI Reasoning (full, untruncated) ──
    reasoning_text = _build_reasoning_panel(result)
    console.print(Panel(
        reasoning_text,
        title="[bold white]AI REASONING[/bold white]",
        border_style="#d2991d",
        padding=(1, 2),
    ))

    # ── BOTTOM PANEL: Verdict ──
    if result.verdict == "FLAGGED":
        verdict_style = "bold red"
        verdict_border = "#ff5555"
        verdict_icon = "[bold red]FLAGGED[/bold red]"
    else:
        verdict_style = "bold green"
        verdict_border = "#3fb950"
        verdict_icon = "[bold green]NOT FLAGGED[/bold green]"

    verdict_content = Text()
    verdict_content.append("Verdict: ", style="bold white")
    verdict_content.append(f"{result.verdict}\n", style=verdict_style)
    verdict_content.append(f"Score: {result.score}/10\n", style="dim")
    verdict_content.append(f"Action: {result.action}\n", style="bold white")

    console.print(Panel(
        verdict_content,
        title=f"{verdict_icon}",
        border_style=verdict_border,
        padding=(1, 2),
    ))

    # ── ACTION DECISION PANEL (if flagged) ──
    if result.verdict == "FLAGGED":
        action_table = Table(
            box=box.ROUNDED,
            border_style="#ff5555",
            show_header=True,
            header_style="bold white",
            expand=True,
        )
        action_table.add_column("Type", style="dim cyan", width=12)
        action_table.add_column("Detail", style="white")

        if result.commands_run:
            for cmd in result.commands_run:
                action_table.add_row("Command", cmd)

        if result.code_changes:
            for cc in result.code_changes:
                action_table.add_row(
                    "Code Change",
                    f"{cc.get('file', '?')}\n  {cc.get('change', '')}"
                )

        if not result.commands_run and not result.code_changes:
            action_table.add_row("Note", f"Action recommended: {result.action}")

        console.print(Panel(
            action_table,
            title="[bold red]ACTION DECISION[/bold red]",
            border_style="#ff5555",
            padding=(1, 2),
        ))

    # Separator
    console.print("")


def render_normal_line(request_id: int, method: str, path: str, ip: str) -> None:
    """Render a one-line normal request entry."""
    mc = _method_color(method)
    console.print(
        f"  [dim]#{request_id:<5d}[/dim] "
        f"[{mc}]{method:6s}[/{mc}] "
        f"[dim]{path[:45]:45s}[/dim] "
        f"[dim]{ip:>15s}[/dim]  "
        f"[dim green]NORMAL[/dim green]"
    )


def render_anomalous_line(request_id: int, method: str, path: str, ip: str,
                          score: int, flagged: bool = False) -> None:
    """Render a one-line anomalous request entry."""
    mc = _method_color(method)
    sigil = "[yellow]??[/yellow]" if not flagged else "[bold red]!![/bold red]"
    label = "[yellow]ANOMALOUS[/yellow]" if not flagged else "[bold red]FLAGGED[/bold red]"
    console.print(
        f"  [bold white]#{request_id:<5d}[/bold white] "
        f"[{mc}]{method:6s}[/{mc}] "
        f"[white]{path[:45]:45s}[/white] "
        f"[dim]{ip:>15s}[/dim]  "
        f"{sigil} {label} [dim](score {score})[/dim]"
    )


def render_subagent_assignment(request_id: int, path: str, agent_rank: int,
                               agent_id: str) -> None:
    """Render which subagent handled a request."""
    console.print(
        f"  [dim]        -> routed to Subagent #{agent_rank} ({agent_id}) for {path}[/dim]"
    )


def _method_color(method: str) -> str:
    """Return rich color for HTTP method."""
    return {
        "GET": "green",
        "POST": "cyan",
        "PUT": "yellow",
        "DELETE": "red",
        "PATCH": "magenta",
        "HEAD": "dim",
        "OPTIONS": "dim",
    }.get(method.upper(), "white")


def _build_reasoning_panel(result) -> Text:
    """Build the full untruncated AI reasoning display."""
    text = Text()

    if result.attack_analysis:
        text.append("ATTACK ANALYSIS:\n", style="bold #e6b47c")
        text.append(f"{result.attack_analysis}\n\n", style="white")

    if result.attacker_assessment:
        text.append("ATTACKER ASSESSMENT:\n", style="bold #e6b47c")
        text.append(f"{result.attacker_assessment}\n\n", style="white")

    if result.reasoning:
        text.append("FULL REASONING:\n", style="bold #e6b47c")
        text.append(f"{result.reasoning}\n", style="white")

    return text
