"""Swarm lifecycle — init, spawn, attach (2026-11-05).

suijin swarm init   → start the coordinator, print the spawn command
suijin swarm spawn  → spawn a background agent, print its ID
suijin swarm attach → open the TUI for a specific agent (or the latest)
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _swarm_db():
    from suijin.server.swarm.coordinator import swarm_db_path

    return swarm_db_path()


def _bus_addr() -> tuple[str, int] | None:
    from suijin.server.mesh.bus import discover_bus

    return discover_bus()


def _spawn_agent(port: int, identity: str = "") -> tuple[str, int]:
    """Spawn a background agent process. Returns (identity, pid)."""
    if not identity:
        identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}"

    # the agent runs headless in the background
    pid_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.pid"
    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "suijin.server.swarm.agent",
            "--coordinator",
            f"127.0.0.1:{port}",
            "--identity",
            identity,
        ],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # survives the parent
    )

    pid_file.write_text(str(proc.pid))
    return identity, proc.pid


def run_swarm_init(args=None) -> int:
    """`suijin swarm init` — start the coordinator and print the spawn command."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    # check if already running
    addr = _bus_addr()
    if addr:
        console.print(f"[green]swarm coordinator already running[/green] on port {addr[1]}")
        console.print(f"\n  spawn an agent:  [cyan]suijin swarm spawn[/cyan]")
        console.print(f"  attach to it:    [cyan]suijin swarm attach[/cyan]")
        console.print(f"  status:          [cyan]suijin swarm status[/cyan]")
        return 0

    # start the coordinator in the background
    console.print("[bold]starting swarm coordinator…[/bold]")

    coordinator_log = Path(tempfile.gettempdir()) / "suijin-swarm-coordinator.log"
    proc = subprocess.Popen(
        [sys.executable, "-c", "from suijin.server.swarm.coordinator import run_swarm_start; run_swarm_start()"],
        stdout=open(coordinator_log, "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    # wait for the bus port file
    from suijin.server.mesh.bus import bus_port_file

    for _ in range(20):
        time.sleep(0.5)
        addr = _bus_addr()
        if addr:
            break

    if not addr:
        console.print("[red]coordinator failed to start — check:[/red]")
        console.print(f"  cat {coordinator_log}")
        return 1

    console.print(
        Panel(
            f"[green]swarm coordinator running[/green]\n\n"
            f"  bus:        tcp://127.0.0.1:{addr[1]}\n"
            f"  database:   {_swarm_db()}\n"
            f"  log:        {coordinator_log}\n\n"
            f"[bold]spawn agents (run this as many times as you want):[/bold]\n"
            f"  [cyan]suijin swarm spawn[/cyan]     ← spawns 1 background agent\n\n"
            f"[bold]other commands:[/bold]\n"
            f"  [cyan]suijin swarm attach[/cyan]   ← open the TUI for the latest agent\n"
            f"  [cyan]suijin swarm status[/cyan]   ← agents, intents, findings\n"
            f"  [cyan]suijin swarm stop[/cyan]     ← stop everything",
            title=" swarm ready ",
            border_style="green",
        )
    )
    return 0


def run_swarm_spawn(args=None) -> int:
    """`suijin swarm spawn` — spawn a background agent, print its ID."""
    from rich.console import Console

    console = Console()

    addr = _bus_addr()
    if not addr:
        console.print("[red]no swarm coordinator running — run [cyan]suijin swarm init[/cyan] first[/red]")
        return 1

    count = int(getattr(args, "count", 1) or 1)
    spawned = []
    for _ in range(count):
        identity, pid = _spawn_agent(addr[1])
        spawned.append((identity, pid))
        time.sleep(0.3)  # stagger starts

    for identity, pid in spawned:
        console.print(f"  [green]✓[/green] agent [cyan]{identity}[/cyan] (pid {pid}) running in background")

    console.print(f"\n  attach:  [cyan]suijin swarm attach {spawned[0][0]}[/cyan]")
    console.print(f"  status:  [cyan]suijin swarm status[/cyan]")
    return 0


def run_swarm_attach(args=None) -> int:
    """`suijin swarm attach [agent-id]` — open the TUI for an agent.

    Without an ID, attaches to the most recently spawned agent.
    The TUI is the standard engagement console — but connected to the
    swarm agent's state instead of a solo engagement."""
    from rich.console import Console

    console = Console()
    identity = str(getattr(args, "agent_id", "") or "").strip()

    # find the agent's log or state
    if not identity:
        # find the most recent agent
        pid_files = sorted(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"), key=lambda p: p.stat().st_mtime)
        if not pid_files:
            console.print("[yellow]no background agents running — spawn one: [cyan]suijin swarm spawn[/cyan][/yellow]")
            return 1
        identity = pid_files[-1].stem.replace("suijin-agent-", "")

    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    if not log_file.is_file():
        console.print(f"[yellow]agent '{identity}' not found[/yellow]")
        # list available agents
        pid_files = sorted(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"))
        if pid_files:
            console.print("\n  running agents:")
            for pf in pid_files[-5:]:
                aid = pf.stem.replace("suijin-agent-", "")
                pid = pf.read_text().strip()
                alive = os.path.exists(f"/proc/{pid}") if os.path.exists("/proc") else True
                try:
                    os.kill(int(pid), 0)
                except OSError:
                    alive = False
                status = "[green]alive[/green]" if alive else "[red]dead[/red]"
                console.print(f"    {aid} (pid {pid}) {status}")
        return 1

    # tail the agent's log live (this IS the attach for now —
    # the full TUI integration is the next step)
    console.print(f"[bold]attached to {identity}[/bold] (Ctrl+C to detach)\n")
    try:
        proc = subprocess.Popen(
            ["tail", "-f", str(log_file)],
            stdin=subprocess.DEVNULL,
        )
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        console.print("\n[dim]detached[/dim]")
    return 0


def run_swarm_stop_all(args=None) -> int:
    """`suijin swarm stop` — stop all agents and the coordinator."""
    from rich.console import Console

    console = Console()

    # kill all background agents
    pid_files = list(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"))
    for pf in pid_files:
        with contextlib.suppress(Exception):
            pid = int(pf.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            pf.unlink()
            console.print(f"  [yellow]stopped agent {pf.stem.replace('suijin-agent-', '')}[/yellow]")

    # stop the coordinator
    from suijin.server.mesh.bus import bus_port_file

    with contextlib.suppress(OSError):
        bus_port_file().unlink(missing_ok=True)

    # kill the coordinator process
    with contextlib.suppress(Exception):
        subprocess.run(
            ["pkill", "-f", "suijin.server.swarm.coordinator"],
            capture_output=True,
            timeout=5,
        )

    console.print("[green]swarm stopped[/green]")
    return 0
