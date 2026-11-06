"""The swarm agent — a headless worker that claims intents (2026-11-05).

One process per agent. Connects to the coordinator's bus. Claims one
intent at a time, works it using the existing tool execution pipeline,
writes facts to the shared database via the bus, publishes findings.

This is the split from the wall memo (slice 4): the planner decides
WHAT to do, this worker does it. The worker cannot plan.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path

from suijin.server.mesh.bus import BusClient


def _pause_flag_path() -> Path:
    """The pause flag file — same mechanism as the engagement console's
    pause-operator flag (checked before each step, not mid-stream)."""
    from suijin.server.mesh.bus import bus_port_file

    return bus_port_file().parent / "pause-swarm"


def _pause_flag_set() -> bool:
    return _pause_flag_path().is_file()


def _swarm_db_path() -> Path:
    from suijin.server.swarm.coordinator import swarm_db_path

    return swarm_db_path()


class SwarmAgent:
    """A worker — claims intents, executes them, reports.

    LOCAL MODE: runs as an asyncio task inside the coordinator (shared
    interpreter — 15 agents = ~200MB total, not 15 × 250MB = 3.75GB).
    REMOTE MODE: runs as a separate process (bootstrap script).
    """

    def __init__(self, coordinator_host: str, coordinator_port: int, identity: str = ""):
        self._host = coordinator_host
        self._port = coordinator_port
        self._identity = identity or f"agent-{os.getpid()}"
        self._bus = BusClient()
        self._running = False
        self._current_intent: dict | None = None
        self._iterations = 0
        self._operator_notes: list[str] = []  # `suijin swarm say` landings
        self._notes_lock = threading.Lock()

    @property
    def identity(self) -> str:
        return self._identity

    def start(self):
        """Connect, register, subscribe, start the work loop."""
        if not self._bus.connect(self._host, self._port, self._identity):
            print(f"cannot connect to coordinator at {self._host}:{self._port}")
            return False

        self._bus.subscribe(f"/agent/{self._identity}/**")  # steering, kill
        self._bus.subscribe("/engagement/**/findings")  # other agents' findings
        self._register()

        self._running = True
        print(f"[{self._identity}] connected to {self._host}:{self._port}")

        # heartbeat thread
        import threading

        hb = threading.Thread(target=self._heartbeat_loop, daemon=True, name="agent-hb")
        hb.start()

        return True

    def stop(self):
        self._running = False
        self._bus.close()

    def run(self):
        """The main work loop: claim → execute → report → repeat."""
        if not self._running and not self.start():
            return 1

        while self._running:
            # check for steering/kill messages
            self._handle_control()

            # claim the next intent
            intent = self._claim_intent()
            if not intent:
                # check the bus for new work (the coordinator broadcasts
                # new intents; agents pick them up immediately instead of
                # sleeping 5s between polls)
                self._handle_control()
                for msg in self._bus.drain(10):
                    topic = str(msg.get("topic") or "")
                    if "/work" in topic or "/swarm/status" in topic:
                        intent = self._claim_intent()
                        if intent:
                            self._current_intent = intent
                            print(f"[{self._identity}] picked up: {intent['text'][:60]}")
                            result = self._execute_intent(intent)
                            self._complete_intent(intent, result)
                            self._current_intent = None
                            continue
                time.sleep(1)  # brief idle between bus checks
                continue

            self._current_intent = intent
            print(f"[{self._identity}] claimed: {intent['text'][:80]}")

            # work it
            result = self._execute_intent(intent)

            # report
            self._complete_intent(intent, result)
            self._current_intent = None

        return 0

    def _register(self):
        """Tell the coordinator I exist."""
        self._bus.publish(
            "/swarm/status",
            {
                "event": "agent_joined",
                "agent": self._identity,
                "pid": os.getpid(),
            },
        )

    def _heartbeat_loop(self):
        while self._running:
            self._bus.publish(
                "/swarm/status",
                {
                    "event": "heartbeat",
                    "agent": self._identity,
                },
            )
            time.sleep(5.0)

    def _handle_control(self):
        """Process steering, kill, and graceful-stop messages."""
        for msg in self._bus.drain(20):
            topic = str(msg.get("topic") or "")
            payload = msg.get("payload") or {}
            event = str(payload.get("event") or "")

            # GRACEFUL STOP: finish the current step, exit clean
            if event == "graceful_stop" or "graceful_stop" in str(payload):
                print(f"[{self._identity}] graceful stop — finishing current step, exiting")
                self._running = False
                # release the current intent back to the queue (unpicked work)
                if self._current_intent:
                    with contextlib.suppress(Exception):
                        import sqlite3

                        db = sqlite3.connect(str(_swarm_db_path()), timeout=5)
                        db.execute(
                            "UPDATE intents SET state = 'open', agent_id = NULL WHERE id = ?",
                            (self._current_intent.get("id"),),
                        )
                        db.commit()
                        db.close()
                return

            # PAUSE: hold before the next step (resume clears the flag)
            if event == "pause" or _pause_flag_set():
                print(f"[{self._identity}] paused — holding (resume to continue)")
                while _pause_flag_set() and self._running:
                    time.sleep(1)

            # RESUME: the bus event clears it too
            if event == "resume":
                pass  # the pause loop above exits on flag clear

            if f"/agent/{self._identity}/kill" in topic:
                print(f"[{self._identity}] killed by operator")
                self._running = False
                return
            if f"/agent/{self._identity}/steer" in topic:
                msg = str(payload.get("message", ""))[:500]
                print(f"[{self._identity}] steering: {msg[:100]}")
                # the interjection lands in the worker loop at the next
                # turn boundary (`suijin swarm say` — printing alone
                # never reached the LLM)
                with self._notes_lock:
                    self._operator_notes.append(msg)

    def _claim_intent(self) -> dict | None:
        """Request the next intent from the coordinator."""
        # For now: direct database claim (single-machine mode)
        # Future: bus.request("claim_intent", agent_id=self._identity)
        with contextlib.suppress(Exception):
            import sqlite3

            from suijin.server.swarm.coordinator import swarm_db_path

            db = sqlite3.connect(str(swarm_db_path()), timeout=5)

            row = db.execute(
                "SELECT id, text, engagement FROM intents WHERE state = 'open' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                db.close()
                return None

            iid, text, engagement = row
            db.execute(
                "UPDATE intents SET state = 'running', agent_id = ?, claimed_at = ? WHERE id = ? AND state = 'open'",
                (self._identity, time.time(), iid),
            )
            db.commit()
            db.close()
            return {"id": iid, "text": text, "engagement": engagement}
        return None

    def _execute_intent(self, intent: dict) -> dict:
        """Work one intent using the shared swarm worker loop.

        One implementation for every worker (process agent, inline agent)
        — see suijin/server/swarm/worker_loop.py for the failure class
        this replaces: prose replies treated as completion, unbound
        `decision` crashes, and a model that never saw the tool catalog.
        """
        self._iterations += 1
        print(f"[{self._identity}] working: {intent['text'][:60]}…")

        from suijin.server.swarm.worker_loop import execute_intent

        def _on_event(turn: int, tool_name: str, result_str: str):
            mark = "CONFIRMED" if "CONFIRMED" in result_str else "result"
            print(f"[{self._identity}] turn {turn}: {tool_name} → {mark} ({len(result_str)} chars)")

        def _on_turn(turn: int) -> str | None:
            with self._notes_lock:
                if not self._operator_notes:
                    return None
                notes = list(self._operator_notes)
                self._operator_notes.clear()
                return "; ".join(notes)

        return execute_intent(
            self._identity,
            intent,
            bus=self._bus,
            on_event=_on_event,
            on_turn=_on_turn,
        )

    def _complete_intent(self, intent: dict, result: dict):
        """Mark the intent complete, persist note + facts, publish."""
        state = result.get("state", "done")
        note = str(result.get("note") or "")
        with contextlib.suppress(Exception):
            import sqlite3

            from suijin.server.swarm.coordinator import swarm_db_path

            db = sqlite3.connect(str(swarm_db_path()), timeout=5)
            db.execute(
                "UPDATE intents SET state = ?, completed_at = ? WHERE id = ?",
                (state, time.time(), intent["id"]),
            )
            # persist facts — the status metric and the report read these
            for fact in result.get("facts", []):
                summary = str(fact.get("summary", ""))[:300]
                evidence = str(fact.get("evidence", ""))[:200]
                db.execute(
                    "INSERT INTO facts (id, summary, evidence, agent_id, engagement, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"fact-{os.urandom(4).hex()}",
                        summary,
                        evidence,
                        self._identity,
                        intent.get("engagement", ""),
                        time.time(),
                    ),
                )
                # CONFIRMED tool results promote to findings so `swarm
                # status` and the report see them (the findings table is
                # what the operator reads first). Exploit-shaped facts
                # only — "CONFIRMED —" is the catalog's verdict format;
                # a bare CONFIRMED elsewhere (command output, sandbox
                # banners) promoted noise on the first field run.
                if "CONFIRMED —" in summary and summary.split(" confirmed:")[0] != "execute_terminal":
                    sev = "unknown"
                    for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                        if level in summary.upper():
                            sev = level.lower()
                            break
                    db.execute(
                        "INSERT INTO findings (id, title, status, severity, agent_id, engagement, created_at) "
                        "VALUES (?, ?, 'confirmed', ?, ?, ?, ?)",
                        (
                            f"find-{os.urandom(4).hex()}",
                            summary[:120],
                            sev,
                            self._identity,
                            intent.get("engagement", ""),
                            time.time(),
                        ),
                    )
            db.commit()
            db.close()

        # publish completion on the bus
        self._bus.publish(
            f"/engagement/{intent.get('engagement', '?')}/work",
            {
                "agent": self._identity,
                "intent": intent["id"],
                "state": state,
            },
        )

        # publish facts
        for fact in result.get("facts", []):
            self._bus.publish(
                f"/engagement/{intent.get('engagement', '?')}/facts",
                {
                    "agent": self._identity,
                    "summary": str(fact.get("summary", ""))[:300],
                    "evidence": str(fact.get("evidence", ""))[:200],
                },
            )

        tail = f" — {note[:120]}" if note else ""
        print(f"[{self._identity}] {state}: {intent['text'][:60]}{tail}")


def run_agent(args=None) -> int:
    """CLI entry: `suijin agent --coordinator host:port`"""
    import argparse

    ap = argparse.ArgumentParser(prog="suijin-agent", description="headless swarm agent")
    ap.add_argument("--coordinator", required=True, help="host:port of the coordinator")
    ap.add_argument("--identity", default="", help="agent identity (default: agent-<pid>)")
    parsed = ap.parse_args(args)

    host, _, port_s = parsed.coordinator.rpartition(":")
    if not host or not port_s.isdigit():
        print(f"invalid coordinator: {parsed.coordinator} (expected host:port)")
        return 1

    agent = SwarmAgent(host, int(port_s), parsed.identity)
    try:
        return agent.run()
    except KeyboardInterrupt:
        agent.stop()
        return 0


if __name__ == "__main__":
    import sys

    sys.exit(run_agent(sys.argv[1:]))
