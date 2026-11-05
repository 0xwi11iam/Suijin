"""Swarm lifecycle — init, spawn, attach (2026-11-05).

suijin swarm init   → start the coordinator, print the spawn command
suijin swarm spawn  → spawn a background agent, print its ID
suijin swarm attach → open the TUI for a specific agent (or the latest)
"""

from __future__ import annotations

import contextlib
import os
import signal
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
        console.print("\n  spawn an agent:  [cyan]suijin swarm spawn[/cyan]")
        console.print("  attach to it:    [cyan]suijin swarm attach[/cyan]")
        console.print("  status:          [cyan]suijin swarm status[/cyan]")
        return 0

    # start the coordinator in the background
    console.print("[bold]starting swarm coordinator…[/bold]")

    coordinator_log = Path(tempfile.gettempdir()) / "suijin-swarm-coordinator.log"
    subprocess.Popen(
        [sys.executable, "-c", "from suijin.server.swarm.coordinator import run_swarm_start; run_swarm_start()"],
        stdout=open(coordinator_log, "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    # wait for the bus port file

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
    """`suijin swarm spawn` — spawn a background agent.
    `suijin swarm spawn --tui` — spawn an agent with the TUI in THIS terminal."""
    from rich.console import Console

    console = Console()

    addr = _bus_addr()
    if not addr:
        console.print("[red]no swarm coordinator running — run [cyan]suijin swarm init[/cyan] first[/red]")
        return 1

    # --tui flag: run the engagement TUI in this terminal, connected to the swarm
    if getattr(args, "tui", False):
        return _spawn_tui(addr, console)

    count = int(getattr(args, "count", 1) or 1)
    spawned = []
    for _ in range(count):
        identity, pid = _spawn_agent(addr[1])
        spawned.append((identity, pid))
        time.sleep(0.3)

    for identity, pid in spawned:
        console.print(f"  [green]✓[/green] agent [cyan]{identity}[/cyan] (pid {pid}) running in background")
    console.print(f"\n  attach:  [cyan]suijin swarm attach {spawned[0][0]}[/cyan]")
    console.print("  status:  [cyan]suijin swarm status[/cyan]")
    return 0


def _spawn_tui(addr: tuple[str, int], console) -> int:
    """Spawn an agent that runs the full engagement TUI in this terminal.
    The OBJECTIVE comes from the swarm's intent queue — no manual input,
    no menu, straight into the engagement. This is the operator's 'open
    a new tab, type one command, agent starts working' flow."""
    identity = f"tui-{int(time.time()) % 10000}-{os.urandom(2).hex()}"

    # claim an intent FIRST — this IS the objective
    intent = _claim_intent_for(identity)
    if not intent:
        console.print("[yellow]no open intents in the swarm queue.[/yellow]")
        console.print("  add work first:")
        console.print("    [cyan]suijin swarm intent \"test http://127.0.0.1:6000 for injection\"[/cyan]")
        console.print("  or spawn without --tui (headless agents idle until work arrives)")
        return 1

    console.print(f"[cyan]swarm agent {identity}[/cyan]")
    console.print(f"  intent: {intent['text'][:80]}")
    console.print("  opening TUI…\n")

    # connect to the bus, register, heartbeats
    from suijin.server.mesh.bus import BusClient

    bus = BusClient()
    if not bus.connect(addr[0], addr[1], identity):
        console.print("[red]cannot connect to coordinator[/red]")
        return 1

    bus.publish("/swarm/status", {"event": "agent_joined", "agent": identity, "pid": os.getpid()})
    bus.subscribe(f"/agent/{identity}/**")

    import threading

    def _heartbeat():
        while True:
            bus.publish("/swarm/status", {"event": "heartbeat", "agent": identity})
            time.sleep(5)

    threading.Thread(target=_heartbeat, daemon=True, name="swarm-hb").start()

    # run the engagement with the intent as the objective — NO MENU,
    # NO OBJECTIVE PICKER, straight in
    try:
        import asyncio

        from suijin.server.redteam.lib.redteamer import run_red_team_async

        config = {
            "objective": intent["text"],
            "target": intent.get("engagement", "swarm"),
            "max_iterations": 100000,
        }
        asyncio.run(run_red_team_async(config, intent["text"]))
        return 0
    except KeyboardInterrupt:
        console.print(f"\n[yellow]agent {identity} disconnected[/yellow]")
        return 0
    finally:
        bus.close()


def _claim_intent_for(agent_id: str) -> dict | None:
    """Claim the next open intent from the swarm database."""
    with contextlib.suppress(Exception):

        import sqlite3

        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        row = db.execute(
            "SELECT id, text, engagement FROM intents WHERE state = 'open' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            db.close()
            return None
        iid, text, engagement = row
        db.execute(
            "UPDATE intents SET state = 'running', agent_id = ?, claimed_at = ? WHERE id = ? AND state = 'open'",
            (agent_id, time.time(), iid),
        )
        db.commit()
        db.close()
        return {"id": iid, "text": text, "engagement": engagement}
    return None


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


def run_swarm_intent(text: str) -> int:
    """`suijin swarm intent "do the thing"` — add work to the queue."""
    from rich.console import Console

    console = Console()
    text = str(text or "").strip()
    if not text:
        console.print("[red]intent text required[/red]")
        console.print('  example: suijin swarm intent "test http://127.0.0.1:6000 for injection"')
        return 1

    import hashlib
    import sqlite3

    iid = "in_" + hashlib.sha1(text.encode()).hexdigest()[:12]
    db = sqlite3.connect(str(_swarm_db()), timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "INSERT OR IGNORE INTO intents (id, text, state, engagement, created_at) VALUES (?, ?, 'open', ?, ?)",
        (iid, text, "swarm", time.time()),
    )
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM intents WHERE state = 'open'").fetchone()[0]
    db.close()

    console.print(f"  [green]✓[/green] intent queued: {text[:80]}")
    console.print(f"  {count} intent(s) waiting — [cyan]suijin swarm spawn --tui[/cyan] to work one")
    return 0
