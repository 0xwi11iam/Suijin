"""Session replay — step through an engagement's thoughts, actions, and
observations from its audit trail.

`suijin replay` renders a live timeline in the terminal: play/pause with
space, scrub with arrows, speed with +/-, quit with q. Non-interactive:
`--list` enumerates engagements, `--export-md` writes the full transcript.
Offline, no API keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from suijin.tools.debrief import load_audits

console = Console()


def list_replays(audit_dir: Path | None = None) -> list[dict]:
    """Audit trails that actually contain iterations, newest first."""
    trails = [t for t in load_audits(audit_dir) if t.get("iterations")]
    return sorted(trails, key=lambda t: str(t.get("started", "")), reverse=True)


def render_markdown(trail: dict) -> str:
    """Full transcript as markdown — the shareable artifact."""
    lines = [
        f"# Replay — {trail.get('engagement', '?')}",
        "",
        f"**Started**: {trail.get('started', '?')}  ",
        f"**Ended**: {trail.get('ended', '—')}  ",
        f"**Actions**: {trail.get('total_actions', 0)} "
        f"({trail.get('successful_actions', 0)} ok, {trail.get('failed_actions', 0)} failed)  ",
        f"**Findings**: {len(trail.get('findings', []))}  ",
        f"**Cost**: ${trail.get('cost_usd', 0.0):.4f}",
        "",
    ]
    for it in trail.get("iterations", []):
        act = it.get("action", {})
        ok = "✔" if act.get("success") else "✘"
        lines.append(f"## Step {it.get('iteration', '?')} — {it.get('phase', '?')} [{ok}]")
        lines.append(f"*{it.get('timestamp', '')}*")
        lines.append("")
        if it.get("thought"):
            lines.append(f"> {str(it['thought'])[:400]}")
            lines.append("")
        lines.append(f"**Tool**: `{act.get('tool', 'none')}`")
        args = act.get("args") or {}
        if args:
            lines.append("")
            lines.append("```json")
            lines.append(str(args)[:2000])
            lines.append("```")
        lines.append("")
        obs = str(it.get("observation", ""))
        if obs:
            lines.append("```")
            lines.append(obs[:4000])
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _render_frame(trail: dict, idx: int, playing: bool, speed: float, total: int) -> Layout:
    iters = trail.get("iterations", [])
    it = iters[idx] if 0 <= idx < len(iters) else {}
    act = it.get("action", {})
    ok = act.get("success")

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")
    status = "[green]▶ playing[/]" if playing else "[yellow]⏸ paused[/]"
    header.add_row(
        f"[bold]{trail.get('engagement', '?')}[/] — step {idx + 1}/{total} · {it.get('phase', '?')}",
        f"{status}  {speed:.1f}x   [dim]space play · ←→ step · +/- speed · q quit[/]",
    )

    thought = Text(str(it.get("thought") or it.get("reasoning") or "")[:600], style="italic dim")

    tool_table = Table.grid(padding=(0, 2))
    tool_table.add_column(style="bold cyan", justify="right")
    tool_table.add_column()
    tool_table.add_row("tool", str(act.get("tool", "none")))
    for k, v in (act.get("args") or {}).items():
        tool_table.add_row(k, str(v)[:120])
    verdict = "[green]success[/]" if ok else "[red]failed[/]"
    tool_table.add_row("result", verdict)

    obs = str(it.get("observation", ""))[:1200]
    if len(str(it.get("observation", ""))) > 1200:
        obs += " …"

    layout = Layout()
    layout.split_column(
        Layout(Panel(header), size=3),
        Layout(Panel(thought, title="thought"), size=8),
        Layout(Panel(tool_table, title="action"), size=10),
        Layout(Panel(obs, title="observation"), minimum_size=8),
    )
    return layout


def _read_key(timeout: float | None) -> str:
    """Single keypress, non-blocking when timeout is set. POSIX only."""
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        if timeout is not None:
            r, _, _ = select.select([sys.stdin], [], [], timeout)
            if not r:
                return ""
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # arrow escape sequence
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq, ch)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run_replay(trail: dict) -> None:
    """Interactive timeline. Falls back to printed transcript if not a TTY."""
    iters = trail.get("iterations", [])
    total = len(iters)
    if total == 0:
        console.print("[yellow]This engagement recorded no iterations.[/]")
        return

    if not sys.stdin.isatty():
        console.print(render_markdown(trail))
        return

    idx, playing, speed, delay = 0, True, 1.0, 1.2
    with Live(
        _render_frame(trail, idx, playing, speed, total), console=console, refresh_per_second=8, screen=True
    ) as live:
        while True:
            key = _read_key(delay / speed if playing else None)
            if key == "q":
                break
            elif key == " ":
                playing = not playing
            elif key == "right":
                idx = min(total - 1, idx + 1)
            elif key == "left":
                idx = max(0, idx - 1)
            elif key in ("+", "="):
                speed = min(8.0, speed * 1.5)
            elif key == "-":
                speed = max(0.25, speed / 1.5)
            elif key == "up":
                idx = max(0, idx - 10)
            elif key == "down":
                idx = min(total - 1, idx + 10)
            elif playing:
                idx = min(total - 1, idx + 1)
                if idx == total - 1:
                    playing = False
            live.update(_render_frame(trail, idx, playing, speed, total))
    console.print(
        f"[dim]replay ended — {total} steps · "
        f"export the transcript with: suijin replay --file "
        f"{trail.get('_file', '')} --export-md[/]"
    )


def pick_engagement(audit_dir: Path | None = None) -> dict | None:
    """Numbered picker over available engagements."""
    trails = list_replays(audit_dir)
    if not trails:
        console.print(
            "[yellow]No replayable engagements — audit trails with iterations live in suijin_agent/audit_trails/.[/]"
        )
        return None
    for i, t in enumerate(trails, 1):
        n = len(t.get("iterations", []))
        console.print(f"  [cyan]{i}[/] {t.get('engagement', '?'):32} {n:>4} steps  {str(t.get('started', ''))[:19]}")
    try:
        choice = int(console.input("[bold]replay which? [/]").strip() or "1")
        return trails[max(0, min(choice, len(trails)) - 1)]
    except (ValueError, EOFError):
        return None
