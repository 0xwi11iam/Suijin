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


def _kill_child_browsers() -> int:
    """NO ORPHANS (operator ruling 2026-11-17): every process this agent
    spawned must die with it. os._exit / SIGKILL skip every finally and
    atexit, so the mcp_playwright chromium child survives as a 200-400MB
    orphan (field: 14 leaked browsers = 16GB, load avg 53). Scan direct
    children of THIS pid for browser cmdlines and kill them."""
    import subprocess as _sp

    me = os.getpid()
    killed = 0
    try:
        out = _sp.run(
            ["pgrep", "-f", "-P", str(me), "chrome-headless-shell|chromium"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    except Exception:
        return 0
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(pid, 9)
            killed += 1
    return killed


def _install_signal_cleanup() -> None:
    """SIGTERM/SIGINT kill spawned children before exiting — the default
    SIGTERM disposition skips Python cleanup entirely."""
    import signal as _signal

    def _clean_exit(signum, _frame):
        _kill_child_browsers()
        os._exit(0)

    for sig in (_signal.SIGTERM, _signal.SIGINT, _signal.SIGHUP):
        with contextlib.suppress(Exception):
            _signal.signal(sig, _clean_exit)


class SwarmAgent:
    """A worker — claims intents, executes them, reports.

    LOCAL MODE: runs as an asyncio task inside the coordinator (shared
    interpreter — 15 agents = ~200MB total, not 15 × 250MB = 3.75GB).
    REMOTE MODE: runs as a separate process (bootstrap script).
    """

    def __init__(self, coordinator_host: str, coordinator_port: int, identity: str = "", mission: str = "", swarm: str = ""):
        self._host = coordinator_host
        self._port = coordinator_port
        self._identity = identity or f"agent-{os.getpid()}"
        self._mission = str(mission or "").strip()  # title-following: worked when the queue runs dry
        self._swarm = str(swarm or "default")  # the console session this agent belongs to
        self._bus = BusClient()
        self._running = False
        self._current_intent: dict | None = None
        self._iterations = 0
        self._operator_notes: list[str] = []  # `suijin swarm say` landings
        self._notes_lock = threading.Lock()
        # PAUSE (2026-11-23): Stop is a pause, not a kill. The event is set
        # by /cmd/pause and cleared by /cmd/resume; _wait_if_paused holds
        # the agent between turns without dying.
        self._paused = threading.Event()

    @property
    def identity(self) -> str:
        return self._identity

    def start(self):
        """Connect, register, subscribe, start the work loop."""
        if not self._bus.connect(self._host, self._port, self._identity):
            print(f"cannot connect to coordinator at {self._host}:{self._port}")
            return False

        self._bus.subscribe(f"/agent/{self._identity}/**")  # targeted steering, kill
        self._bus.subscribe("/engagement/**/findings")  # other agents' findings
        # THE COMMAND BUS — control-plane commands reach EVERY agent
        # (host, boxed, future cloud nodes) on one topic tree. The first
        # field bug: graceful_stop went to /swarm/status which agents
        # never subscribed to — host agents died via pid files, boxed
        # agents survived a `swarm stop` (5 of 10 stopped, 2026-11-06).
        self._bus.subscribe("/cmd/**")
        self._register()

        self._running = True
        print(f"[{self._identity}] connected to {self._host}:{self._port}")

        # heartbeat thread
        import threading

        hb = threading.Thread(target=self._heartbeat_loop, daemon=True, name="agent-hb")
        hb.start()

        return True

    def stop(self):
        # NO ORPHANS: the browser child dies with the agent on every path
        _kill_child_browsers()
        self._running = False
        self._bus.close()

    def run(self):
        """The main work loop: claim → execute → report → repeat.

        TITLE-FOLLOWING (operator design, 2026-11-06): an agent with a
        standing mission (the engagement title) works THE TITLE when the
        queue runs dry — no intent mechanics required, the agent just
        follows the title. Intents, when queued, take priority."""
        if not self._running and not self.start():
            return 1

        mission_runs = 0
        while self._running:
            # check for steering/kill messages
            self._handle_control()
            self._wait_if_paused()

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
                if self._mission and mission_runs < 3:
                    # queue dry → follow the title (bounded: 3 mission
                    # passes, then idle for new intents/steering)
                    mission_runs += 1
                    with contextlib.suppress(Exception):
                        import sqlite3 as _sq3

                        _db = _sq3.connect(str(_swarm_db_path()), timeout=5)
                        _db.execute("UPDATE agents SET current_intent = ? WHERE id = ?",
                                    (f"mission: {self._mission[:100]}", self._identity))
                        _db.commit()
                        _db.close()
                    print(f"[{self._identity}] queue dry — following the title: {self._mission[:60]}")
                    # engagement = THIS AGENT'S SWARM (field 2026-11-17): the
                    # hardcoded "default" sent every tool event to the shared
                    # _default engagement — the console session never saw any
                    # tool activity. The swarm name IS the console session id.
                    mi = {"id": f"mission-{mission_runs}", "text": self._mission, "engagement": self._swarm}
                    result = self._execute_intent(mi)
                    self._complete_intent(mi, result)
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
                "swarm": self._swarm,
            },
        )

    def _heartbeat_loop(self):
        # COORDINATOR-DEAD DETECTION (2026-11-15): if the bus publish fails
        # repeatedly, the coordinator is gone and nobody can steer or stop
        # this agent — exit instead of hunting forever as an orphan.
        failed = 0
        while self._running:
            ok = self._bus.publish(
                "/swarm/status",
                {
                    "event": "heartbeat",
                    "agent": self._identity,
                },
            )
            failed = 0 if ok else failed + 1
            if failed >= 6:
                print(f"[{self._identity}] coordinator unreachable — exiting (orphan guard)")
                _kill_child_browsers()
                self._running = False
                with contextlib.suppress(OSError):
                    os._exit(0)

            time.sleep(5.0)

    def _targets_me(self, payload: dict) -> bool:
        """A /cmd/* broadcast may carry a swarm or agent id; honor only ours."""
        payload = payload or {}
        swarm = str(payload.get("swarm") or "")
        agent = str(payload.get("agent") or "")
        if swarm and swarm != self._swarm:
            return False
        if agent and agent != self._identity:
            return False
        return True

    def _wait_if_paused(self) -> None:
        """Hold between turns while paused. Stop/delete still work; resume
        clears the hold; steering still lands in the notes."""
        if not self._paused.is_set():
            return
        print(f"[{self._identity}] paused — holding (resume to continue)", flush=True)
        while self._running and self._paused.is_set():
            time.sleep(0.5)
            for msg in self._bus.drain(20):
                topic = str(msg.get("topic") or "")
                payload = msg.get("payload") or {}
                if not self._targets_me(payload):
                    continue
                if f"/agent/{self._identity}/kill" in topic or "/cmd/stop" in topic or "/cmd/delete" in topic:
                    print(f"[{self._identity}] stop while paused — exiting", flush=True)
                    self._running = False
                    return
                if "/cmd/resume" in topic:
                    self._paused.clear()
                    print(f"[{self._identity}] resumed", flush=True)
                elif "/cmd/steer" in topic:
                    note = str(payload.get("message") or "")[:500]
                    if note:
                        with self._notes_lock:
                            self._operator_notes.append(note)

    def _handle_control(self):
        """Process steering, pause/resume, kill, and graceful-stop messages."""
        for msg in self._bus.drain(20):
            topic = str(msg.get("topic") or "")
            payload = msg.get("payload") or {}
            event = str(payload.get("event") or "")

            # GRACEFUL STOP: finish the current step, exit clean
            if (event == "graceful_stop" or "graceful_stop" in str(payload)) and self._targets_me(payload):
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

            # PAUSE: hold between turns (resume clears it). The global pause
            # flag still applies to every agent; a /cmd/pause carries a swarm
            # or agent id so one session does not pause the whole fleet.
            if (event == "pause" or _pause_flag_set()) and self._targets_me(payload):
                print(f"[{self._identity}] pause requested — holding", flush=True)
                self._paused.set()
            if event == "resume" and self._targets_me(payload):
                self._paused.clear()

            if f"/agent/{self._identity}/kill" in topic:
                print(f"[{self._identity}] killed by operator")
                self._running = False
                return
            # ── the command bus (broadcast control) ─────────────────
            if "/cmd/" in topic:
                if not self._targets_me(payload):
                    continue
                cmd = topic.rsplit("/", 1)[-1].lower()
                if cmd in ("stop", "graceful_stop", "shutdown", "delete"):
                    print(f"[{self._identity}] command: {cmd} — exiting")
                    self._running = False
                    return
                elif cmd == "pause":
                    print(f"[{self._identity}] command: pause — holding", flush=True)
                    self._paused.set()
                elif cmd == "resume":
                    print(f"[{self._identity}] command: resume", flush=True)
                    self._paused.clear()
                elif cmd == "steer":
                    msg = str((payload or {}).get("message") or "")[:500]
                    if msg:
                        print(f"[{self._identity}] command steer: {msg[:80]}")
                        with self._notes_lock:
                            self._operator_notes.append(msg)
                elif cmd == "mission":
                    new_mission = str((payload or {}).get("mission") or "")[:500]
                    if new_mission:
                        self._mission = new_mission
                        print(f"[{self._identity}] command: mission updated → {new_mission[:60]}")

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

            # SWARM-SCOPED CLAIM (field 2026-11-17: an agent claimed ANY
            # open intent — a fresh engagement on a public search host locked onto a
            # stale engagement intent left in the queue by an old hunt. An
            # agent only ever claims intents of its OWN swarm; title-
            # following covers everything else.)
            row = db.execute(
                "SELECT id, text, engagement FROM intents "
                "WHERE state = 'open' AND swarm = ? ORDER BY created_at LIMIT 1",
                (self._swarm,),
            ).fetchone()
            if not row:
                db.close()
                return None

            iid, text, engagement = row
            db.execute(
                "UPDATE intents SET state = 'running', agent_id = ?, claimed_at = ? WHERE id = ? AND state = 'open'",
                (self._identity, time.time(), iid),
            )
            # the console's lane column reads this — an agent working an
            # intent must not show 'idle' (operator field report)
            db.execute("UPDATE agents SET current_intent = ? WHERE id = ?",
                       (text[:120], self._identity))
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

        def _on_event(turn: int, tool_name: str, result_str: str, tool_args=None, thought: str = ""):
            # KILL/STOP CHECK PER TURN (field 2026-11-17: control messages
            # only drained at intent boundaries — a title-following mission
            # runs many turns without one, so Delete/Stop left agents
            # running for minutes). The turn boundary is the natural
            # checkpoint: drain, and honor terminal commands instantly.
            with contextlib.suppress(Exception):
                for msg in self._bus.drain(20):
                    topic = str(msg.get("topic") or "")
                    payload = msg.get("payload") or {}
                    if not self._targets_me(payload):
                        continue
                    if f"/agent/{self._identity}/kill" in topic or "/cmd/stop" in topic or "/cmd/delete" in topic:
                        print(f"[{self._identity}] {topic} — exiting now (mid-mission stop)")
                        self._running = False
                        _kill_child_browsers()
                        os._exit(0)
                    if "/cmd/pause" in topic:
                        self._paused.set()
                    elif "/cmd/resume" in topic:
                        self._paused.clear()

            # USAGE ON THE BUS: token spend flows to every node (web
            # console, remote suijins) — the data layer reads the same
            # tally locally; this is the wire version
            with contextlib.suppress(Exception):
                from suijin.modules.providers.lib import get_usage

                u = get_usage()
                self._bus.publish(
                    f"/engagement/{intent.get('engagement') or 'default'}/usage",
                    {"agent": self._identity, "turn": turn, "calls": u.get("calls", 0),
                     "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)},
                )
            # EMIT the operator console's events.jsonl format — the swarm
            # console's details pane renders the SAME panel (thought →
            # tool+args → observation). Written into the engagement dir,
            # which is MOUNTED for boxed agents: one file, host and boxed
            # alike, exactly like the operator console reads.
            # NATIVE event_log records (suijin/server/agent/lib/event_log
            # schema) — the operator console's pane renders these natively
            # (thought→tool+args→observation, prettified, correct colors);
            # the ad-hoc format rendered as raw JSON (operator field
            # report). Also published on the bus: /engagement/<eng>/events
            # (centralized flow — host, boxed, cloud clients alike).
            with contextlib.suppress(Exception):
                import json as _json

                from suijin.server.platform.lib.workspace import WORKSPACE_DIR

                _eng = str(intent.get("engagement") or self._swarm or "default")
                if _eng in ("default", ""):
                    _eng = "_default"  # the workspace's default engagement slug
                ev_dir = Path(WORKSPACE_DIR).parent / "engagements" / _eng
                ev_dir.mkdir(parents=True, exist_ok=True)
                tid = f"{self._identity[:14]}-{turn}-{str(tool_name)[:10]}"
                ok = "error" not in str(result_str or "").lower()[:60]
                recs = [
                    {"kind": "tool.call", "id": tid, "name": str(tool_name), "args": tool_args or {},
                     "agent": self._identity, "seq": turn, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                    {"kind": "tool.result", "id": tid, "output": " ".join(str(result_str or "").split())[:600],
                     "ok": ok, "agent": self._identity, "seq": turn, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                ]
                if thought:
                    recs.insert(0, {"kind": "assistant.message", "content": str(thought)[:400],
                                    "agent": self._identity, "seq": turn,
                                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                with (ev_dir / "events.jsonl").open("a") as f:
                    for r in recs:
                        f.write(_json.dumps(r) + "\n")
                for r in recs:
                    self._bus.publish(f"/engagement/{_eng}/events", r)
            # THE ACTIVITY STREAM — an operator watching attach/console must
            # see WHAT happened, not a char count (field report: "they are
            # working quickly but I can't see what's actually going on").
            # shape: turn N: > tool {key: arg-head} -> result-head
            arg_hint = ""
            try:
                a = dict(tool_args or {})
                if a:
                    k = sorted(a.keys())[0]
                    arg_hint = f" {{{k}: {str(a[k])[:40]}}}"
            except Exception:  # noqa: BLE001
                pass
            head = " ".join(str(result_str or "").split())[:90]
            mark = "CONFIRMED" if "CONFIRMED" in str(result_str) else ""
            print(f"[{self._identity}] turn {turn}: > {tool_name}{arg_hint} -> {head}{(' [' + mark + ']') if mark else ''}")

        def _on_turn(turn: int) -> str | None:
            self._wait_if_paused()
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
            db.execute("UPDATE agents SET current_intent = NULL WHERE id = ?", (self._identity,))
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
                    import re as _re

                    # Title cleanup at the WRITE path (2026-11-23): old rows
                    # stored `catalog_exploit confirmed: EXP-002 CONFIRMED —
                    # LOW CVSS 3.7 : <title>`. Keep the real title; the EXP
                    # id rides the stable finding id (no duplicate on rerun).
                    m = _re.search(
                        r"(EXP-\d{1,4})\s+CONFIRMED\s+—\s*"
                        r"(?:(?:CRITICAL|HIGH|MEDIUM|LOW|INFO)\s+)?"
                        r"(?:CVSS\s+[\d.]+\s*)?:?\s*(.+)$",
                        summary, _re.I,
                    )
                    clean_title = (m.group(2) if m else summary).strip()
                    clean_title = _re.sub(
                        r"^(?:catalog_exploit|exploit)\s+confirmed:\s*", "",
                        clean_title, flags=_re.I,
                    ).strip() or summary[:1000]
                    sev = "unknown"
                    for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                        if level in summary.upper():
                            sev = level.lower()
                            break
                    fid = (
                        f"find-{m.group(1).lower()}-{intent.get('engagement', '')}"
                        if m else f"find-{os.urandom(4).hex()}"
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO findings (id, title, status, severity, agent_id, engagement, created_at) "
                        "VALUES (?, ?, 'confirmed', ?, ?, ?, ?)",
                        (
                            fid,
                            clean_title[:1000],
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
    ap.add_argument("--mission", default="", help="standing mission (the engagement title) — worked when the queue is empty")
    ap.add_argument("--swarm", default="", help="the swarm/session this agent belongs to (console mapping)")
    parsed = ap.parse_args(args)

    host, _, port_s = parsed.coordinator.rpartition(":")
    if not host or not port_s.isdigit():
        print(f"invalid coordinator: {parsed.coordinator} (expected host:port)")
        return 1

    from suijin.server.platform.lib.memory_guard import start_guard as _memory_guard

    _memory_guard("agent")  # overnight guardrail: RSS ceiling per agent
    agent = SwarmAgent(host, int(port_s), parsed.identity, mission=parsed.mission, swarm=parsed.swarm)
    _install_signal_cleanup()
    try:
        return agent.run()
    except KeyboardInterrupt:
        agent.stop()
        return 0
    finally:
        # every exit path — no orphans
        _kill_child_browsers()


if __name__ == "__main__":
    import sys

    sys.exit(run_agent(sys.argv[1:]))
