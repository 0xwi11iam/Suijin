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
        """Start the bus, database, governor, and health monitoring."""
        self.bus.start()
        self._db = self._open_db()
        self.pool.start()
        self.batcher.start()

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
