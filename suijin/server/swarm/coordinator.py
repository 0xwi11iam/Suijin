"""The swarm coordinator — work distribution, health, shared state (2026-11-05).

Runs as `suijin swarm start`. One process. Hosts:
  - the message bus (BusBroker)
  - the shared engagement database (SQLite WAL)
  - work assignment (intents → agents)
  - health monitoring (heartbeat timeouts → requeue intents)
  - per-target rate limiting (across ALL agents)

This is the brain that lets 100 agents work as one system.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from pathlib import Path

from suijin.server.mesh.bus import BusBroker, BusClient

# ── the shared database (SQLite WAL) ──────────────────────────────────

SWARM_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    joined_at REAL,
    last_heartbeat REAL,
    current_intent TEXT,
    status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    state TEXT DEFAULT 'open',
    agent_id TEXT,
    engagement TEXT,
    parent_fact TEXT,
    claimed_at REAL,
    completed_at REAL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    evidence TEXT,
    asset_id TEXT,
    agent_id TEXT,
    observed BOOLEAN DEFAULT 1,
    confidence REAL DEFAULT 0.8,
    engagement TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT DEFAULT 'pending',
    severity TEXT DEFAULT 'info',
    exchange_ids TEXT,
    agent_id TEXT,
    engagement TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS rate_limits (
    host TEXT PRIMARY KEY,
    tokens REAL,
    last_refill REAL,
    max_per_sec REAL DEFAULT 10.0
);
"""


def swarm_db_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = Path(WORKSPACE_DIR).parent / "swarm"
    d.mkdir(parents=True, exist_ok=True)
    return d / "swarm.db"


class SwarmCoordinator:
    """The coordinator process — `suijin swarm start`."""

    def __init__(self, bus_port: int = 0):
        self.bus = BusBroker(port=bus_port)
        self._db: sqlite3.Connection | None = None
        self._running = False
        self._health_thread: threading.Thread | None = None
        self._heartbeat_timeout = 15.0  # seconds without heartbeat = dead
        self._agents: dict[str, dict] = {}  # id → {identity, last_hb, intent}
        # RESOURCE GOVERNOR (2026-11-05): shared process pool + disk batcher
        from suijin.server.swarm.governor import DiskBatcher, ProcessPool

        self.pool = ProcessPool()
        self.batcher = DiskBatcher()

    @property
    def port(self) -> int:
        return self.bus.port

    def start(self):
        """Start the bus, database, governor, and health monitoring.
        Also subscribes to /swarm/status to auto-register agents."""
        self.bus.start()
        self._db = self._open_db()
        self.pool.start()
        self.batcher.start()

        # AUTO-REGISTER: listen for agent_joined and heartbeat on the bus
        # and keep the database in sync — this is what `suijin swarm status`
        # reads. Without this, agents connect to the bus but never appear.
        self._bus_listener = _BusEventListener(self)
        self._bus_listener.start()

        # subscribe to swarm control topics
        self.bus._route("/swarm/status", {"event": "coordinator_started", "port": self.bus.port})

        self._running = True
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True, name="swarm-health")
        self._health_thread.start()

        # write the port file for discovery
        from suijin.server.mesh.bus import bus_port_file

        with contextlib.suppress(OSError):
            bus_port_file().parent.mkdir(parents=True, exist_ok=True)
            bus_port_file().write_text(str(self.bus.port))

    def stop(self):
        self._running = False
        self.bus.stop()
        self.pool.stop()
        self.batcher.stop()
        with contextlib.suppress(Exception):
            if self._db:
                self._db.close()

    def _open_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(swarm_db_path()), timeout=10, check_same_thread=False)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript(SWARM_SCHEMA)
        db.commit()
        return db

    # ── work assignment ──────────────────────────────────────────

    def _run_inline_agent(self, identity: str, swarm: str = "default"):
        """A full agent work loop running INSIDE the coordinator's process.
        Shares the interpreter (one import of all tools), but has its own
        intent, its own LLM calls, its own tool execution. Same power as a
        separate process, 1/25th the memory."""
        print(f"[{identity}] inline agent started (swarm: {swarm})", flush=True)
        while self._running:
            intent = self.claim_next_intent(identity)
            if not intent:
                time.sleep(3)
                continue

            print(f"[{identity}] claimed: {intent['text'][:60]}", flush=True)

            from suijin.server.swarm.worker_loop import execute_intent

            result = execute_intent(identity, intent)
            self.complete_intent(identity, intent["id"], result.get("state", "done"))
            print(f"[{identity}] {result.get('state', 'done')}: {intent['text'][:50]}", flush=True)

    def claim_next_intent(self, agent_id: str) -> dict | None:
        """Give this agent the next open intent. Marks it running."""
        with contextlib.suppress(sqlite3.Error):
            row = self._db.execute(
                "SELECT id, text, engagement, parent_fact FROM intents WHERE state = 'open' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            iid, text, engagement, parent_fact = row
            self._db.execute(
                "UPDATE intents SET state = 'running', agent_id = ?, claimed_at = ? WHERE id = ? AND state = 'open'",
                (agent_id, time.time(), iid),
            )
            self._db.execute("UPDATE agents SET current_intent = ? WHERE id = ?", (iid, agent_id))
            self._db.commit()
            return {"id": iid, "text": text, "engagement": engagement, "parent_fact": parent_fact}
        return None

    def complete_intent(self, agent_id: str, intent_id: str, state: str, note: str = ""):
        """Mark an intent done/exhausted/blocked."""
        if state not in ("done", "exhausted", "blocked"):
            state = "done"
        with contextlib.suppress(sqlite3.Error):
            self._db.execute(
                "UPDATE intents SET state = ?, completed_at = ? WHERE id = ? AND agent_id = ?",
                (state, time.time(), intent_id, agent_id),
            )
            self._db.execute("UPDATE agents SET current_intent = NULL WHERE id = ?", (agent_id,))
            self._db.commit()

    # ── health monitoring ────────────────────────────────────────

    def register_agent(self, agent_id: str, identity: str):
        with contextlib.suppress(sqlite3.Error):
            self._db.execute(
                "INSERT OR REPLACE INTO agents (id, identity, joined_at, last_heartbeat, status) VALUES (?, ?, ?, ?, 'active')",
                (agent_id, identity, time.time(), time.time()),
            )
            self._db.commit()
        self._agents[agent_id] = {"identity": identity, "last_hb": time.time(), "intent": None}

    def heartbeat(self, agent_id: str):
        with contextlib.suppress(sqlite3.Error):
            self._db.execute("UPDATE agents SET last_heartbeat = ? WHERE id = ?", (time.time(), agent_id))
            self._db.commit()
        if agent_id in self._agents:
            self._agents[agent_id]["last_hb"] = time.time()

    def _health_loop(self):
        """Every 5s: check for dead agents, requeue their intents."""
        while self._running:
            time.sleep(5.0)
            now = time.time()
            for aid, info in list(self._agents.items()):
                if now - info["last_hb"] > self._heartbeat_timeout:
                    # dead agent — requeue its intent
                    with contextlib.suppress(sqlite3.Error):
                        self._db.execute(
                            "UPDATE intents SET state = 'open', agent_id = NULL WHERE agent_id = ? AND state = 'running'",
                            (aid,),
                        )
                        self._db.execute("UPDATE agents SET status = 'dead' WHERE id = ?", (aid,))
                        self._db.commit()
                    self.bus._route("/swarm/status", {"event": "agent_dead", "agent": aid})
                    self._agents.pop(aid, None)

    # ── rate limiting (per-target, across ALL agents) ───────────

    def check_rate(self, host: str) -> float:
        """Shared token bucket. Returns seconds to wait (0 = go)."""
        max_per_sec = 10.0  # default; configurable per host
        now = time.time()
        with contextlib.suppress(sqlite3.Error):
            row = self._db.execute("SELECT tokens, last_refill FROM rate_limits WHERE host = ?", (host,)).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT OR IGNORE INTO rate_limits (host, tokens, last_refill, max_per_sec) VALUES (?, ?, ?, ?)",
                    (host, max_per_sec, now, max_per_sec),
                )
                self._db.commit()
                return 0.0
            tokens, last_refill = row
            elapsed = now - last_refill
            tokens = min(max_per_sec, tokens + elapsed * max_per_sec)
            if tokens >= 1.0:
                self._db.execute(
                    "UPDATE rate_limits SET tokens = tokens - 1.0, last_refill = ? WHERE host = ?",
                    (now, host),
                )
                self._db.commit()
                return 0.0
            self._db.execute("UPDATE rate_limits SET tokens = ?, last_refill = ? WHERE host = ?", (tokens, now, host))
            self._db.commit()
            return (1.0 - tokens) / max_per_sec
        return 0.0

    # ── status ───────────────────────────────────────────────────

    def status(self) -> dict:
        with contextlib.suppress(sqlite3.Error):
            agents = self._db.execute("SELECT COUNT(*) FROM agents WHERE status = 'active'").fetchone()[0]
            open_i = self._db.execute("SELECT COUNT(*) FROM intents WHERE state = 'open'").fetchone()[0]
            running_i = self._db.execute("SELECT COUNT(*) FROM intents WHERE state = 'running'").fetchone()[0]
            done_i = self._db.execute("SELECT COUNT(*) FROM intents WHERE state = 'done'").fetchone()[0]
            confirmed = self._db.execute("SELECT COUNT(*) FROM findings WHERE status = 'confirmed'").fetchone()[0]
            facts = self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            return {
                "agents": agents,
                "intents_open": open_i,
                "intents_running": running_i,
                "intents_done": done_i,
                "findings_confirmed": confirmed,
                "facts": facts,
                "bus_port": self.bus.port,
                "bus_stats": self.bus.stats,
            }
        return {"agents": 0, "bus_port": self.bus.port, "bus_stats": self.bus.stats}


class _BusEventListener:
    """Listens on the bus for /swarm/status messages and keeps the
    coordinator's database in sync. This is what makes `suijin swarm
    status` show real agents."""

    def __init__(self, coordinator: "SwarmCoordinator"):
        self._coord = coordinator
        self._client = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        from suijin.server.mesh.bus import BusClient

        self._client = BusClient()
        if self._client.connect("127.0.0.1", self._coord.bus.port, "coordinator-listener"):
            self._client.subscribe("/swarm/**")
            self._running = True
            self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="bus-listener")
            self._thread.start()

    def stop(self):
        self._running = False
        if self._client:
            self._client.close()

    def _listen_loop(self):
        """Drain bus messages and sync the database."""
        while self._running:
            msgs = self._client.drain(50) if self._client else []
            for msg in msgs:
                self._handle(msg)
            time.sleep(0.2)

    def _handle(self, msg: dict):
        topic = str(msg.get("topic") or "")
        payload = msg.get("payload") or {}
        event = str(payload.get("event") or "")

        if "/swarm/status" in topic:
            if event == "agent_joined":
                agent_id = str(payload.get("agent") or "")
                identity = agent_id
                self._coord.register_agent(agent_id, identity)
            elif event == "heartbeat":
                agent_id = str(payload.get("agent") or "")
                self._coord.heartbeat(agent_id)
            elif event == "agent_dead":
                pass  # handled by the health loop
            elif event == "spawn_agents":
                # SPAWN IN-PROCESS AGENTS: N work loops as asyncio tasks in
                # this coordinator — one interpreter, full tool access,
                # ~10MB per agent instead of ~250MB
                count = int(payload.get("count") or 0)
                swarm = str(payload.get("swarm") or "default")
                identities = payload.get("identities") or []
                for identity in identities[:count]:
                    import threading

                    t = threading.Thread(
                        target=self._run_inline_agent,
                        args=(identity, swarm),
                        daemon=True,
                        name=f"agent-{identity}",
                    )
                    t.start()
                    self.register_agent(identity, identity)

        elif "/work" in topic:
            # an agent completed an intent — update the database
            intent_id = str(payload.get("intent") or "")
            agent_id = str(payload.get("agent") or "")
            state = str(payload.get("state") or "")
            if intent_id and agent_id:
                self._coord.complete_intent(agent_id, intent_id, state)

        elif "/findings" in topic:
            # a finding landed — store it
            agent_id = str(payload.get("agent") or payload.get("from") or "")
            finding_text = str(payload.get("finding") or payload.get("msg") or "")
            if finding_text:
                import hashlib

                fid = "fi_" + hashlib.sha1(finding_text.encode()).hexdigest()[:12]
                with contextlib.suppress(sqlite3.Error):
                    self._coord._db.execute(
                        "INSERT OR REPLACE INTO findings (id, title, status, agent_id, created_at) VALUES (?, ?, 'confirmed', ?, ?)",
                        (fid, finding_text[:300], agent_id, time.time()),
                    )
                    self._coord._db.commit()

        elif "/facts" in topic:
            # a fact was recorded — store it
            agent_id = str(payload.get("agent") or payload.get("from") or "")
            summary = str(payload.get("summary") or "")
            evidence = str(payload.get("evidence") or "")
            if summary:
                import hashlib

                fact_id = "fa_" + hashlib.sha1(summary.encode()).hexdigest()[:12]
                with contextlib.suppress(sqlite3.Error):
                    self._coord._db.execute(
                        "INSERT OR IGNORE INTO facts (id, summary, evidence, agent_id, created_at) VALUES (?, ?, ?, ?, ?)",
                        (fact_id, summary[:500], evidence[:300], agent_id, time.time()),
                    )
                    self._coord._db.commit()


# ── the CLI entry point ───────────────────────────────────────────────


def run_swarm_start(args=None) -> int:
    """`suijin swarm start` — start the coordinator in the foreground."""
    from rich.console import Console

    console = Console()
    coord = SwarmCoordinator()
    coord.start()

    console.print("[green]swarm coordinator started[/green]")
    console.print(f"  bus: tcp://127.0.0.1:{coord.port}")
    console.print(f"  database: {swarm_db_path()}")
    console.print(f"\n  agents connect with: suijin agent --coordinator 127.0.0.1:{coord.port}")
    console.print("  status: [cyan]suijin swarm status[/cyan] (from another terminal)")
    console.print("\n  [dim]press Ctrl+C to stop[/dim]")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]stopping…[/yellow]")
        coord.stop()
        console.print("[green]stopped[/green]")
        return 0


def run_swarm_status(args=None) -> int:
    """`suijin swarm status` — one-line snapshot."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    # check if the bus is running
    from suijin.server.mesh.bus import discover_bus

    bus_addr = discover_bus()
    if not bus_addr:
        console.print("[yellow]no swarm coordinator running[/yellow]")
        console.print("  start one: [cyan]suijin swarm start[/cyan]")
        return 1

    # connect and read status
    client = BusClient()
    if not client.connect(bus_addr[0], bus_addr[1], "status-probe"):
        console.print("[red]coordinator unreachable[/red]")
        return 1

    # TODO: when the coordinator handles a STATUS op, query it
    # For now read the database directly
    db = sqlite3.connect(str(swarm_db_path()), timeout=5)
    db.execute("PRAGMA journal_mode=WAL")

    table = Table(title="swarm status", show_header=True)
    table.add_column("metric", style="cyan")
    table.add_column("value", style="bold")

    with contextlib.suppress(sqlite3.Error):
        for label, q in [
            ("agents", "SELECT COUNT(*) FROM agents WHERE status = 'active'"),
            ("intents open", "SELECT COUNT(*) FROM intents WHERE state = 'open'"),
            ("intents running", "SELECT COUNT(*) FROM intents WHERE state = 'running'"),
            ("findings confirmed", "SELECT COUNT(*) FROM findings WHERE status = 'confirmed'"),
            ("facts", "SELECT COUNT(*) FROM facts"),
        ]:
            val = db.execute(q).fetchone()[0]
            table.add_row(label, str(val))

    console.print(table)
    db.close()
    client.close()
    return 0
