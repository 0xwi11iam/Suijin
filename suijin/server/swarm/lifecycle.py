"""Swarm lifecycle — init with intents, named swarms, spawn/attach (2026-11-05).

suijin swarm init                    → start; if old intents exist, ask before wiping
suijin swarm init "test X for Y"    → start + queue this intent immediately
suijin swarm intent "do X"           → append to the intent list (never replaces)
suijin swarm spawn [--swarm name]    → spawn an agent to the named swarm (default: first)
suijin swarm spawn --tui             → same but with the full TUI in this terminal
suijin swarm status                  → all swarms, all agents, all intents
suijin swarm stop                    → stop everything
"""

from __future__ import annotations

import contextlib
import hashlib
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


def _db():
    """Open the swarm database."""
    db = sqlite3.connect(str(_swarm_db()), timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    # ensure the swarm column exists (migration for old databases)
    with contextlib.suppress(sqlite3.Error):
        db.execute("ALTER TABLE intents ADD COLUMN swarm TEXT DEFAULT 'default'")
    with contextlib.suppress(sqlite3.Error):
        db.execute("ALTER TABLE agents ADD COLUMN swarm TEXT DEFAULT 'default'")
    db.commit()
    return db


def _add_intent(text: str, swarm: str = "default") -> str:
    """Add an intent to a swarm's queue. Returns the intent id."""
    iid = "in_" + hashlib.sha1(f"{swarm}:{text}".encode()).hexdigest()[:12]
    db = _db()
    db.execute(
        "INSERT OR IGNORE INTO intents (id, text, state, engagement, created_at, swarm) VALUES (?, ?, 'open', ?, ?, ?)",
        (iid, text, swarm, time.time(), swarm),
    )
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM intents WHERE state = 'open' AND swarm = ?", (swarm,)).fetchone()[0]
    db.close()
    return iid, count


def _list_swarms() -> list[dict]:
    """All named swarms with their intent counts."""
    db = _db()
    rows = db.execute(
        "SELECT swarm, COUNT(*) as total, SUM(CASE WHEN state = 'open' THEN 1 ELSE 0 END) as open_count "
        "FROM intents GROUP BY swarm ORDER BY swarm"
    ).fetchall()
    agents = db.execute(
        "SELECT swarm, COUNT(*) FROM agents WHERE status = 'active' GROUP BY swarm"
    ).fetchall()
    agent_map = {r[0]: r[1] for r in agents}
    db.close()
    return [
        {"name": r[0], "intents_total": r[1], "intents_open": r[2], "agents": agent_map.get(r[0], 0)}
        for r in rows
    ]


def _first_swarm() -> str | None:
    """The first swarm with open intents, or None."""
    db = _db()
    row = db.execute(
        "SELECT swarm FROM intents WHERE state = 'open' ORDER BY created_at LIMIT 1"
    ).fetchone()
    db.close()
    return row[0] if row else None


# ── init ──────────────────────────────────────────────────────────────


def run_swarm_init(args=None) -> int:
    """`suijin swarm init [intent text]` — start the coordinator.

    With no args: if old intents exist, ask before wiping.
    With text: start + queue that intent immediately.
    With --name: create a named swarm.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm

    console = Console()
    intent_text = " ".join(getattr(args, "text", []) or []).strip()
    swarm_name = str(getattr(args, "name", "") or "default").strip() or "default"

    # check if already running
    addr = _bus_addr()
    already_running = addr is not None

    if not already_running:
        console.print("[bold]starting swarm coordinator…[/bold]")
        coordinator_log = Path(tempfile.gettempdir()) / "suijin-swarm-coordinator.log"
        subprocess.Popen(
            [sys.executable, "-c", "from suijin.server.swarm.coordinator import run_swarm_start; run_swarm_start()"],
            stdout=open(coordinator_log, "w"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        for _ in range(20):
            time.sleep(0.5)
            addr = _bus_addr()
            if addr:
                break
        if not addr:
            console.print(f"[red]coordinator failed — check {coordinator_log}[/red]")
            return 1

    # check for existing intents (only wipe if operator confirms)
    db = _db()
    existing = db.execute("SELECT COUNT(*) FROM intents WHERE state IN ('open', 'running')").fetchone()[0]
    db.close()

    if existing > 0 and not intent_text:
        console.print(f"\n[yellow]{existing} intent(s) already in the queue:[/yellow]")
        db = _db()
        for r in db.execute("SELECT text, state, swarm FROM intents WHERE state IN ('open','running') LIMIT 5").fetchall():
            console.print(f"  [{r[1]}] {r[0][:60]}  [dim]({r[2]})[/dim]")
        db.close()
        if not Confirm.ask("  Wipe them and start fresh?", default=False):
            console.print("[green]kept — adding to the existing queue[/green]")
        else:
            db = _db()
            db.execute("DELETE FROM intents")
            db.execute("DELETE FROM agents")
            db.commit()
            db.close()
            console.print("[green]wiped — clean slate[/green]")

    # queue the intent if provided
    if intent_text:
        _, count = _add_intent(intent_text, swarm_name)
        console.print(f"  [green]✓[/green] intent queued: {intent_text[:70]}")
        console.print(f"  {count} intent(s) in '{swarm_name}' queue")

    # print the summary
    swarms = _list_swarms()
    swarm_lines = "\n".join(
        f"    {s['name']:<12} {s['intents_open']} open / {s['intents_total']} total · {s['agents']} agents"
        for s in swarms
    ) or "    (empty — add work: suijin swarm intent \"...\")"

    console.print(
        Panel(
            f"[green]swarm coordinator running[/green] on port {addr[1]}\n\n"
            f"[bold]swarms:[/bold]\n{swarm_lines}\n\n"
            f"[bold]spawn agents:[/bold]\n"
            f"  [cyan]suijin swarm spawn[/cyan]          ← background agent (first swarm)\n"
            f"  [cyan]suijin swarm spawn --tui[/cyan]    ← full TUI in this terminal\n"
            f"  [cyan]suijin swarm spawn --swarm name[/cyan] ← specific swarm\n\n"
            f"[bold]add work:[/bold]\n"
            f"  [cyan]suijin swarm intent \"test X for Y\"[/cyan]\n\n"
            f"[bold]other:[/bold]\n"
            f"  [cyan]suijin swarm status[/cyan]  ·  [cyan]suijin swarm stop[/cyan]",
            title=" swarm ready ",
            border_style="green",
        )
    )
    return 0


# ── intent ────────────────────────────────────────────────────────────


def run_swarm_intent(text: str, swarm: str = "default") -> int:
    """`suijin swarm intent "do X"` — append to the queue (never replaces)."""
    from rich.console import Console

    console = Console()
    text = str(text or "").strip()
    if not text:
        console.print("[red]intent text required[/red]")
        console.print('  example: suijin swarm intent "test http://127.0.0.1:6000 for injection"')
        return 1

    _, count = _add_intent(text, swarm)
    console.print(f"  [green]✓[/green] queued ({swarm}): {text[:70]}")
    console.print(f"  {count} intent(s) waiting — [cyan]suijin swarm spawn --tui[/cyan]")
    return 0


# ── spawn ─────────────────────────────────────────────────────────────


def run_swarm_spawn(args=None) -> int:
    """`suijin swarm spawn [--tui] [--swarm name]` — spawn an agent."""
    from rich.console import Console

    console = Console()
    addr = _bus_addr()
    if not addr:
        console.print("[red]no coordinator — run [cyan]suijin swarm init[/cyan] first[/red]")
        return 1

    swarm_name = str(getattr(args, "swarm", "") or "").strip()
    use_tui = bool(getattr(args, "tui", False))

    # default: first swarm with open intents
    if not swarm_name:
        swarm_name = _first_swarm() or "default"

    if use_tui:
        return _spawn_tui(addr, swarm_name, console)

    # background agent
    identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}"
    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    pid_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.pid"

    proc = subprocess.Popen(
        [sys.executable, "-m", "suijin.server.swarm.agent", "--coordinator", f"127.0.0.1:{addr[1]}", "--identity", identity],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    console.print(f"  [green]✓[/green] {identity} (pid {proc.pid}) → swarm '{swarm_name}' (background)")
    console.print(f"  attach: [cyan]suijin swarm attach {identity}[/cyan]")
    return 0


def _spawn_tui(addr: tuple[str, int], swarm_name: str, console) -> int:
    """Spawn with the full TUI — claims an intent and launches."""
    identity = f"tui-{int(time.time()) % 10000}-{os.urandom(2).hex()}"

    intent = _claim_intent_for(identity, swarm_name)
    if not intent:
        console.print(f"[yellow]no open intents in '{swarm_name}'[/yellow]")
        console.print('  add work: [cyan]suijin swarm intent "test X for Y"[/cyan]')
        return 1

    console.print(f"[cyan]{identity}[/cyan] → {intent['text'][:70]}")
    console.print("  launching…\n")

    from suijin.server.mesh.bus import BusClient

    bus = BusClient()
    if not bus.connect(addr[0], addr[1], identity):
        console.print("[red]cannot connect[/red]")
        return 1
    bus.publish("/swarm/status", {"event": "agent_joined", "agent": identity, "pid": os.getpid()})

    import threading

    def _hb():
        while True:
            bus.publish("/swarm/status", {"event": "heartbeat", "agent": identity})
            time.sleep(5)

    threading.Thread(target=_hb, daemon=True).start()

    try:
        import asyncio

        from suijin.server.redteam.lib.redteamer import run_red_team_async

        # LOAD THE OPERATOR'S ACTUAL CONFIG — the old bare dict fell back
        # to deepseek (the code default) instead of the operator's provider
        from suijin.modules.platform.lib.config_loader import load_config

        config = load_config()
        config["objective"] = intent["text"]
        config["target"] = intent.get("engagement", swarm_name)
        asyncio.run(run_red_team_async(config, intent["text"]))
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        bus.close()


def _claim_intent_for(agent_id: str, swarm: str = "") -> dict | None:
    """Claim the next open intent, optionally filtered by swarm."""
    with contextlib.suppress(Exception):
        db = _db()
        if swarm:
            row = db.execute(
                "SELECT id, text, engagement FROM intents WHERE state = 'open' AND swarm = ? ORDER BY created_at LIMIT 1",
                (swarm,),
            ).fetchone()
        else:
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


# ── attach / stop / status ────────────────────────────────────────────


def run_swarm_attach(args=None) -> int:
    """`suijin swarm attach [agent-id]` — tail an agent's output."""
    from rich.console import Console

    console = Console()
    identity = str(getattr(args, "agent_id", "") or "").strip()

    if not identity:
        pid_files = sorted(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"), key=lambda p: p.stat().st_mtime)
        if not pid_files:
            console.print("[yellow]no agents — [cyan]suijin swarm spawn[/cyan][/yellow]")
            return 1
        identity = pid_files[-1].stem.replace("suijin-agent-", "")

    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    if not log_file.is_file():
        console.print(f"[yellow]agent '{identity}' not found[/yellow]")
        return 1

    console.print(f"[bold]attached to {identity}[/bold] (Ctrl+C to detach)\n")
    try:
        proc = subprocess.Popen(["tail", "-f", str(log_file)], stdin=subprocess.DEVNULL)
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        console.print("\n[dim]detached[/dim]")
    return 0


def run_swarm_stop_all(args=None) -> int:
    """`suijin swarm stop` — stop all agents and the coordinator."""
    from rich.console import Console

    console = Console()
    pid_files = list(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"))
    for pf in pid_files:
        with contextlib.suppress(Exception):
            os.kill(int(pf.read_text().strip()), signal.SIGTERM)
            pf.unlink()
            console.print(f"  [yellow]stopped {pf.stem.replace('suijin-agent-', '')}[/yellow]")

    from suijin.server.mesh.bus import bus_port_file

    with contextlib.suppress(OSError):
        bus_port_file().unlink(missing_ok=True)
    with contextlib.suppress(Exception):
        subprocess.run(["pkill", "-f", "suijin.server.swarm.coordinator"], capture_output=True, timeout=5)
    console.print("[green]swarm stopped[/green]")
    return 0
