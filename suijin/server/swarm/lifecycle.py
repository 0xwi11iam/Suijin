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


def _wait_for_schema(timeout: float = 15.0) -> bool:
    """The coordinator creates the db schema shortly after the bus port
    appears — proceeding before it is the 'no such table: intents' race
    (field run, 2026-11-06). Poll until the intents table exists.

    swarm_db_path is imported LAZILY: it is not a module-level name
    here, and the bare reference NameError'd every poll — suppressed,
    the wait 'timed out' while the schema sat ready (the cold-hunt
    failure, found by instrumenting the INSTALLED copy)."""
    import sqlite3

    from suijin.server.swarm.coordinator import swarm_db_path

    deadline = time.time() + timeout
    _dbg = os.environ.get("SUIJIN_SCHEMA_DEBUG")
    attempt = 0
    while time.time() < deadline:
        try:
            db = sqlite3.connect(str(swarm_db_path()), timeout=3)
            db.execute("SELECT COUNT(*) FROM intents").fetchone()
            db.close()
            return True
        except Exception as e:
            if _dbg and attempt < 5:
                print(f"[schema-wait] {attempt}: {e.__class__.__name__}: {str(e)[:80]} path={swarm_db_path()}", flush=True)
            attempt += 1
        time.sleep(0.3)
    return False


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
    if not _wait_for_schema():
        console.print("[red]coordinator up but the database never became ready[/red]")
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


def run_swarm_intent(text: str, swarm: str = "default", force: bool = False) -> int:
    """`suijin swarm intent "do X"` — append to the queue (never replaces).
    Broadcasts on the bus so idle agents pick it up immediately."""
    from rich.console import Console

    console = Console()
    text = str(text or "").strip()
    if not text:
        console.print("[red]intent text required[/red]")
        console.print('  example: suijin swarm intent "test http://127.0.0.1:6000 for injection"')
        return 1

    # PRIOR-WORK GATE: refuse intents that re-derive buried findings
    # (the 2026-11-05 corpse class). --force overrides with a note.
    with contextlib.suppress(Exception):
        from suijin.server.swarm.prior_work import gate_intent

        allowed, note = gate_intent(text, swarm, force=force)
        if not allowed:
            console.print(f"[red]{note}[/red]")
            return 1
        if note:
            console.print(f"[yellow]{note}[/yellow]")

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


def _spawn_agent(port: int, mission: str = "") -> tuple[str, int]:
    """Spawn one agent as a background HOST process. Returns (identity, pid).
    mission = the engagement title: worked when the queue runs dry
    (title-following — agents just follow the title, no intents needed)."""
    identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}"
    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    pid_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.pid"
    argv = [sys.executable, "-u", "-m", "suijin.server.swarm.agent",
            "--coordinator", f"127.0.0.1:{port}", "--identity", identity]
    if mission:
        argv += ["--mission", mission]
    proc = subprocess.Popen(
        argv,
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
    sandbox_n = max(0, int(getattr(args, "sandbox", 0) or 0))

    if use_tui:
        return _spawn_tui(addr, swarm_name, console)

    if sandbox_n:
        # agents IN BOXES — the hypervisor path. Everything configured
        # on the mac (auth, settings, model) is mounted in: same files,
        # live. Sub-second per agent from the warm pile.
        from suijin.server.box import hypervisor as hv

        if not hv.engine_available():
            console.print("[red]no container engine — sandbox spawn needs orbstack or docker[/red]")
            return 1
        if not hv.image_exists():
            console.print("[yellow]sandbox image missing — building it now (one-time)…[/yellow]")
            hv.build_image(on_log=console.print)
        box_addr = hv.coordinator_addr_for_boxes(addr[1])
        names = []
        for _ in range(sandbox_n):
            identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}-sbx"
            _mission = str(getattr(args, "mission", "") or "")
            name = hv.spawn_sandbox_agent(box_addr, identity, swarm_name or "default", on_log=console.print, mission=_mission)
            if name:
                names.append(name)
                console.print(f"  [green]✓[/green] {identity} in box [cyan]{name}[/cyan]")
        console.print(f"  [green]{len(names)}/{sandbox_n}[/green] boxed agent(s) live at {box_addr}")
        if names:
            console.print("  [cyan]suijin sandbox ls[/cyan] · [cyan]suijin swarm status[/cyan]")
            import threading as _th

            _th.Thread(target=hv.pool_refill, kwargs={"coordinator": box_addr}, daemon=True).start()
        return 0 if names else 1

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
        # single: spawn as a host process (same as before)
        identity, pid = _spawn_agent(addr[1], mission=str(getattr(args, "mission", "") or ""))
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


def _resolve_agent_fragment(fragment: str) -> tuple[str, str] | None:
    """Any natural fragment → ('host', agent-id) or ('box', box-name).

    Accepts: full agent id, agent fragment ('5476-f85a'), box name
    ('suijin-sbx-default-agent-5476-f85a-sbx'), box fragment. Host
    agents resolve via pid files; boxed agents via docker ps."""
    frag = str(fragment or "").strip().lower()
    if not frag:
        return None

    # host agents: pid files
    for pf in sorted(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        aid = pf.stem.replace("suijin-agent-", "")
        if frag in aid.lower():
            with contextlib.suppress(Exception):
                os.kill(int(pf.read_text().strip()), 0)  # alive check
                return ("host", aid)

    # boxed agents: docker containers
    with contextlib.suppress(Exception):
        r = subprocess.run(["docker", "ps", "--filter", "name=suijin-sbx", "--format",
                            "{{.Names}}"], capture_output=True, text=True, timeout=10)
        for name in (r.stdout or "").split():
            if frag in name.lower():
                return ("box", name)

    # bare fragment like '5476-f85a' → try as agent-<frag> against boxes
    with contextlib.suppress(Exception):
        r = subprocess.run(["docker", "ps", "--filter", "name=suijin-sbx", "--format",
                            "{{.Names}}"], capture_output=True, text=True, timeout=10)
        for name in (r.stdout or "").split():
            if "agent-" + frag in name:
                return ("box", name)
    return None


def run_swarm_attach(args=None) -> int:
    """`suijin swarm attach <anything>` — watch a live agent.

    Accepts any natural fragment: full agent id, '5476-f85a', or a box
    name from `sandbox ls`. Host agents follow their log file; boxed
    agents follow `docker logs -f` (the first cut only knew host files —
    boxed agents were unviewable, the 'kind of broken' report)."""
    from rich.console import Console

    console = Console()
    frag = str(getattr(args, "agent_id", "") or "").strip()

    hit = _resolve_agent_fragment(frag) if frag else None
    if not hit:
        # nothing matched (or nothing given) — list every watchable agent
        console.print("[yellow]watchable agents (any fragment works):[/yellow]")
        for pf in sorted(Path(tempfile.gettempdir()).glob("suijin-agent-*.pid"),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            aid = pf.stem.replace("suijin-agent-", "")
            alive = "  "
            with contextlib.suppress(Exception):
                os.kill(int(pf.read_text().strip()), 0)
                alive = "●"
            console.print(f"  host {alive} {aid}")
        with contextlib.suppress(Exception):
            r = subprocess.run(["docker", "ps", "--filter", "name=suijin-sbx", "--format",
                                "{{.Names}}"], capture_output=True, text=True, timeout=10)
            for name in (r.stdout or "").split():
                if "pool" not in name:
                    console.print(f"  box  ● {name}")
        return 1

    kind, ref = hit
    console.print(f"[bold]viewing {ref}[/bold] ({kind}) — Ctrl+C to detach\n")
    try:
        if kind == "host":
            log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{ref}.log"
            proc = subprocess.Popen(["tail", "-n", "40", "-f", str(log_file)],
                                    stdin=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(["docker", "logs", "-f", "--tail", "40", ref],
                                    stdin=subprocess.DEVNULL)
        proc.wait()
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
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


def run_swarm_hunt(title: str = "", host_n: int = 0, boxed_n: int = 0) -> int:
    """`suijin swarm hunt "<title>"` — start a hunt by TITLE.

    The title IS the work (operator design, 2026-11-06): the swarm is
    named for the engagement title and agents follow it directly as
    their standing mission — no intents required. Intents can still be
    queued later for specific lanes; they take priority when present.
    CLI twin of the TUI new-hunt flow."""
    from rich.console import Console

    console = Console()
    title = str(title or "").strip()
    if not title:
        console.print('[red]usage: suijin swarm hunt "what to hunt" --host N --boxed N[/red]')
        return 1
    addr = _bus_addr()
    if not addr:
        console.print("[yellow]no coordinator — starting one…[/yellow]")
        import subprocess as _sp

        _sp.run(["suijin", "swarm", "init", title], timeout=120)
        addr = _bus_addr()
        if not addr or not _wait_for_schema():
            console.print("[red]could not start the swarm[/red]")
            return 1
    host_n = max(0, int(host_n or 0))
    boxed_n = max(0, int(boxed_n or 0))
    if not host_n and not boxed_n:
        host_n = 4  # sensible default: 4 host agents
    console.print(f'  hunt: [bold]{title[:70]}[/bold]')
    console.print(f"  agents: {host_n} host + {boxed_n} boxed — the title is their standing mission")
    rc = 0
    if boxed_n:
        import types as _types

        r = run_swarm_spawn(_types.SimpleNamespace(
            tui=False, swarm="default", count=0, sandbox=boxed_n, mission=title))
        rc = rc or r
    for _ in range(host_n):
        import types as _types

        r = run_swarm_spawn(_types.SimpleNamespace(
            tui=False, swarm="default", count=1, sandbox=0, mission=title))
        rc = rc or r
    console.print("  [cyan]suijin swarm console[/cyan] to watch · [cyan]suijin swarm say[/cyan] to steer")
    return rc


def run_swarm_control() -> int:
    """`suijin swarm control` — the full-screen hub (state + actions)."""
    from suijin.server.swarm.control import run_hub_cmd

    return run_hub_cmd()


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
    # 4. remove engagement boxes — their agents exited via /cmd/stop;
    # the containers themselves die here (data survives: mounted).
    # The 5-of-10 field bug: pid files only ever knew host agents.
    with contextlib.suppress(Exception):
        from suijin.server.box import hypervisor as _hv

        n_boxes = 0
        for b in _hv.sandbox_ls():
            if "pool" not in b["name"]:
                if _hv.sandbox_stop(b["name"]):
                    n_boxes += 1
        if n_boxes:
            console.print(f"[yellow]{n_boxes} agent box(es) removed[/yellow]")
    console.print("[green]swarm stopped — clean[/green]")
    return 0
