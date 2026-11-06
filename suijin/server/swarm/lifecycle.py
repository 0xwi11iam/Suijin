"""Swarm lifecycle — init with intents, named swarms, spawn/attach (2026-11-05).

suijin swarm init                    → start; if old intents exist, ask before wiping
suijin swarm init "test X for Y"    → start + queue this intent immediately
suijin swarm intent "do X"           → append to the intent list (never replaces)
suijin swarm spawn [--swarm name]    → spawn an agent; --tui shows it in this terminal
                                       (same agent either way — --tui is just a view)
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
            f"  [cyan]suijin swarm spawn[/cyan]            ← run an agent (view it: swarm view)\n"
            f"  [cyan]suijin swarm spawn --tui[/cyan]      ← same agent, shown in this terminal\n"
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
    """`suijin swarm intent "do X"` — append to the queue (never replaces).
    Broadcasts on the bus so idle agents pick it up immediately."""
    from rich.console import Console

    console = Console()
    text = str(text or "").strip()
    if not text:
        console.print("[red]intent text required[/red]")
        console.print('  example: suijin swarm intent "test http://127.0.0.1:6000 for injection"')
        return 1

    _, count = _add_intent(text, swarm)

    # broadcast on the bus — idle agents see it instantly
    addr = _bus_addr()
    if addr:
        from suijin.server.mesh.bus import BusClient

        bc = BusClient()
        if bc.connect(addr[0], addr[1], "intent-broadcaster"):
            bc.publish(f"/engagement/{swarm}/work", {"event": "new_intent", "text": text, "swarm": swarm})
            bc.close()

    console.print(f"  [green]✓[/green] queued ({swarm}): {text[:70]}")
    console.print(f"  {count} intent(s) waiting — idle agents pick up automatically")
    return 0


# ── spawn ─────────────────────────────────────────────────────────────


def _spawn_agent(port: int) -> tuple[str, int]:
    """Spawn one agent as a background process. Returns (identity, pid)."""
    identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}"
    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    pid_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.pid"
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "suijin.server.swarm.agent", "--coordinator", f"127.0.0.1:{port}", "--identity", identity],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))
    return identity, proc.pid


def run_swarm_spawn(args=None) -> int:
    """`suijin swarm spawn [-N count]` — spawn agents.
    count=1: one background process. count>1: lightweight asyncio agents
    inside the coordinator's process (share one Python interpreter —
    15 agents = ~200MB total, not 15 × 250MB = 3.75GB)."""
    from rich.console import Console

    console = Console()
    addr = _bus_addr()
    if not addr:
        console.print("[red]no coordinator — run [cyan]suijin swarm init[/cyan] first[/red]")
        return 1

    swarm_name = str(getattr(args, "swarm", "") or "").strip()
    use_tui = bool(getattr(args, "tui", False))
    count = max(1, int(getattr(args, "count", 1) or 1))

    if use_tui:
        return _spawn_tui(addr, swarm_name, console)

    if count > 1:
        # multiple: tell the COORDINATOR to spawn N work loops as asyncio
        # tasks inside its process — one interpreter, one import of suijin,
        # N independent agent loops each with full tool access
        spawned = []
        for i in range(count):
            identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}-{i}"
            _add_intent(f"auto-agent-{identity}", swarm_name or "default")  # register in db
            spawned.append(identity)

        # ask the coordinator to run them (via a bus control message)
        from suijin.server.mesh.bus import BusClient

        bc = BusClient()
        if bc.connect(addr[0], addr[1], "spawner"):
            bc.publish("/swarm/status", {
                "event": "spawn_agents",
                "count": count,
                "swarm": swarm_name or "default",
                "identities": spawned,
            })
            bc.close()

        console.print(f"  [green]✓[/green] {len(spawned)} agent(s) requested (shared process, full tools)")
        console.print(f"  memory: ~{15 * len(spawned)}MB total (one interpreter)")
        console.print(f"  [cyan]suijin swarm status[/cyan] to see them")
    elif count == 1:
        # single: spawn as a process (same as before)
        identity, pid = _spawn_agent(addr[1])
        console.print(f"  [green]✓[/green] {identity} (pid {pid}) → '{swarm_name}' (background)")
    else:
        # multiple: spawn as asyncio tasks in the coordinator's process
        # — they share one interpreter, memory stays flat
        from suijin.server.mesh.bus import BusClient

        spawned = []
        for i in range(count):
            identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}-{i}"
            bc = BusClient()
            if bc.connect(addr[0], addr[1], identity):
                bc.publish("/swarm/status", {"event": "agent_joined", "agent": identity, "pid": -1})
                spawned.append(identity)
                bc.close()
            time.sleep(0.05)

        # write the pid file pointing at the coordinator's process
        pid_file = Path(tempfile.gettempdir()) / "suijin-swarm-agents.pid"
        pid_file.write_text(",".join(spawned))

        console.print(f"  [green]✓[/green] {len(spawned)} agent(s) spawned (shared process)")
        console.print(f"  memory: ~{10 * len(spawned)}MB total (shared interpreter)")
        console.print(f"  [cyan]suijin swarm status[/cyan] to see them all")
    return 0


def _spawn_tui(addr: tuple[str, int], swarm_name: str, console) -> int:
    """Spawn with the full TUI — claims an intent and launches."""
    identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}"

    intent = _claim_intent_for(identity, swarm_name)
    if not intent:
        # No open intents — but maybe one is RUNNING. Multiple agents on
        # the same target is the whole point of a swarm. Create a parallel
        # lane on the same work instead of erroring out.
        intent = _parallel_lane(identity, swarm_name, console)
        if not intent:
            console.print("[yellow]no work in the queue[/yellow]")
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

        # LOAD THE OPERATOR'S ACTUAL CONFIG — the old bare dict fell back
        # to deepseek (the code default) instead of the operator's provider
        from suijin.modules.platform.lib.config_loader import load_config
        from suijin.server.redteam.lib.redteamer import run_red_team_async

        config = load_config()
        config["objective"] = intent["text"]
        config["target"] = intent.get("engagement", swarm_name)
        asyncio.run(run_red_team_async(config, intent["text"]))
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        bus.close()


def _parallel_lane(agent_id: str, swarm: str, console) -> dict | None:
    """No open intents but some are RUNNING — create a parallel lane on
    the same target. Multiple agents working the same target from
    different angles IS the swarm. The operator's complaint: 'why are
    they hogging, they can share if there's nothing.'"""
    with contextlib.suppress(Exception):
        db = _db()
        # find the most recent running intent in this swarm
        if swarm:
            row = db.execute(
                "SELECT id, text, engagement FROM intents WHERE state = 'running' AND swarm = ? ORDER BY claimed_at DESC LIMIT 1",
                (swarm,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT id, text, engagement FROM intents WHERE state = 'running' ORDER BY claimed_at DESC LIMIT 1"
            ).fetchone()

        if not row:
            db.close()
            return None

        orig_id, orig_text, engagement = row
        # create a parallel lane: same target, this agent's own copy
        lane_text = f"{orig_text} (parallel lane — {agent_id})"
        lane_id = "in_" + hashlib.sha1(f"{agent_id}:{orig_text}".encode()).hexdigest()[:12]
        db.execute(
            "INSERT OR IGNORE INTO intents (id, text, state, engagement, created_at, swarm, agent_id, claimed_at) "
            "VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
            (lane_id, lane_text, engagement, time.time(), swarm or "default", agent_id, time.time()),
        )
        db.commit()
        db.close()
        console.print(f"  [dim]joining running work: {orig_text[:50]}[/dim]")
        return {"id": lane_id, "text": orig_text, "engagement": engagement}
    return None


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
    """`suijin swarm view [agent-id]` — watch a live agent.

    Agents are agents — TUI-spawned or headless, same process, same
    engagement, same output. view tails the agent's log live; if the
    agent isn't running, lists the ones that are."""
    from rich.console import Console

    console = Console()
    identity = str(getattr(args, "agent_id", "") or "").strip()

    pid_files = sorted(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"), key=lambda p: p.stat().st_mtime)
    if not pid_files:
        console.print("[yellow]no agents running — [cyan]suijin swarm spawn[/cyan][/yellow]")
        return 1

    if not identity:
        identity = pid_files[-1].stem.replace("suijin-agent-", "")

    # list running agents if the requested one isn't found
    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    if not log_file.is_file():
        console.print(f"[yellow]agent '{identity}' not found. Running:[/yellow]")
        for pf in pid_files[-10:]:
            aid = pf.stem.replace("suijin-agent-", "")
            alive = True
            with contextlib.suppress(Exception):
                os.kill(int(pf.read_text().strip()), 0)
            console.print(f"    [cyan]suijin swarm view {aid}[/cyan]")
        return 1

    console.print(f"[bold]viewing {identity}[/bold] (Ctrl+C to exit)\n")
    try:
        proc = subprocess.Popen(["tail", "-n", "40", "-f", str(log_file)], stdin=subprocess.DEVNULL)
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        console.print("\n[dim]detached[/dim]")
    return 0


def run_swarm_pause(args=None) -> int:
    """`suijin swarm pause` — graceful pause: the agent's CURRENT tool call /
    iteration finishes, then it holds. Not a SIGSTOP mid-stream (which
    breaks TCP connections and loses in-flight LLM tokens).
    Resume: suijin swarm resume — agents continue from the pause point."""
    from rich.console import Console

    console = Console()

    # write a pause flag to the mesh dir — agents' run loops check it
    # before starting the next iteration (same mechanism as the
    # engagement console's ESC ESC pause)
    from suijin.server.mesh.bus import bus_port_file

    flag = bus_port_file().parent / "pause-swarm"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(str(time.time()))
    console.print(f"[yellow]pause flag set[/yellow] — agents finish their current step, then hold")
    console.print(f"  [cyan]suijin swarm resume[/cyan] to continue")
    return 0


def run_swarm_resume(args=None) -> int:
    """`suijin swarm resume` — clear the pause flag; agents continue."""
    from rich.console import Console

    from suijin.server.mesh.bus import bus_port_file

    console = Console()
    flag = bus_port_file().parent / "pause-swarm"
    with contextlib.suppress(OSError):
        flag.unlink(missing_ok=True)
    # broadcast resume on the bus
    addr = _bus_addr()
    if addr:
        from suijin.server.mesh.bus import BusClient

        bc = BusClient()
        if bc.connect(addr[0], addr[1], "resume-broadcaster"):
            bc.publish("/swarm/status", {"event": "resume"})
            bc.close()
    console.print("[green]resumed — agents continue from where they held[/green]")
    return 0


def run_swarm_say(args=None) -> int:
    """`suijin swarm say "message"` — message every live agent.

    The message lands as an OPERATOR INTERJECTION inside each agent's
    current conversation at the next turn boundary (not just a log
    line) — steer the swarm mid-engagement.
    """
    from rich.console import Console

    console = Console()
    message = str(getattr(args, "message", "") or "").strip()
    if not message:
        console.print("[red]usage: suijin swarm say \"what to say\"[/red]")
        return 1
    addr = _bus_addr()
    if not addr:
        console.print("[red]no swarm — run [cyan]suijin swarm init[/cyan] first[/red]")
        return 1

    # live agents: heartbeat in the last 30s
    import sqlite3

    from suijin.server.swarm.coordinator import swarm_db_path

    agents = []
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(swarm_db_path()), timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        cutoff = time.time() - 30
        agents = [r[0] for r in db.execute(
            "SELECT id FROM agents WHERE last_heartbeat > ? ORDER BY id", (cutoff,)
        ).fetchall()]
        db.close()

    if not agents:
        console.print("[yellow]no live agents to message[/yellow]")
        return 1

    from suijin.server.mesh.bus import BusClient

    bc = BusClient()
    if not bc.connect(addr[0], addr[1], f"operator-{os.urandom(2).hex()}"):
        console.print("[red]cannot reach the swarm bus[/red]")
        return 1
    for identity in agents:
        bc.publish(f"/agent/{identity}/steer", {"message": message})
    bc.close()

    console.print(f"[green]✓ messaged {len(agents)} agent(s):[/green] {message}")
    console.print("[dim]agents receive it as an operator interjection on their next turn[/dim]")
    return 0


def run_swarm_stop_all(args=None) -> int:
    """`suijin swarm stop` — GRACEFUL shutdown: broadcast a stop event on
    the bus, agents see it before their next step and exit clean (their
    current tool call finishes), THEN kill stragglers, THEN stop the
    coordinator. One command, no error messages."""
    from rich.console import Console

    console = Console()

    # 1. broadcast graceful stop — agents exit their loops cleanly
    addr = _bus_addr()
    if addr:
        from suijin.server.mesh.bus import BusClient

        bc = BusClient()
        if bc.connect(addr[0], addr[1], "stop-broadcaster"):
            bc.publish("/swarm/status", {"event": "graceful_stop"})
            bc.close()
        console.print("[yellow]stop broadcast — agents finishing their current step…[/yellow]")
        time.sleep(2)  # let them land

    # 2. SIGTERM any stragglers (they should have exited in step 1)
    pid_files = list(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"))
    stopped = 0
    for pf in pid_files:
        with contextlib.suppress(Exception):
            pid = int(pf.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            pf.unlink()
            stopped += 1
    if stopped:
        console.print(f"[yellow]{stopped} agent(s) terminated[/yellow]")

    # 3. stop the coordinator last (it hosts the bus)
    from suijin.server.mesh.bus import bus_port_file

    with contextlib.suppress(OSError):
        bus_port_file().unlink(missing_ok=True)
    with contextlib.suppress(Exception):
        subprocess.run(["pkill", "-f", "suijin.server.swarm.coordinator"], capture_output=True, timeout=5)
    console.print("[green]swarm stopped — clean[/green]")
    return 0
