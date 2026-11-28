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


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
        # WHAT THE OPERATOR SEES (2026-11-28). The engagement's journal is the console's only view
        # of this agent. It used to be written only when a tool call COMPLETED, so a turn that died
        # on the model call left nothing: the agent reported "blocked" to its own log file,
        # re-queued itself, and the console said "Thinking…" for as long as the process lived.
        self._announced: set[tuple[str, int]] = set()  # (intent id, turn) whose thought + call are in the journal
        self._reasoned: set[tuple[str, int]] = set()  # (intent id, turn) where the model's own reasoning was shown
        self._state = ""  # the last state the journal was told about
        self._model = ""  # "provider:model" of its last model call
        self._blocked = False  # a failure the operator must fix: no new work until they speak
        self._mission_runs = 0  # title-following passes since the operator last spoke
        self._journal_lock = threading.Lock()

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

        self._mission_runs = 0
        while self._running:
            # check for steering/kill messages
            self._handle_control()
            self._wait_if_paused()

            # claim the next intent
            intent = self._claim_work()
            if not intent:
                # check the bus for new work (the coordinator broadcasts
                # new intents; agents pick them up immediately instead of
                # sleeping 5s between polls)
                self._handle_control()
                for msg in self._bus.drain(10):
                    topic = str(msg.get("topic") or "")
                    if "/work" in topic or "/swarm/status" in topic:
                        intent = self._claim_work()
                        if intent:
                            self._current_intent = intent
                            print(f"[{self._identity}] picked up: {intent['text'][:60]}")
                            self._say_state("working", f"picked up: {intent['text'][:100]}")
                            result = self._execute_intent(intent)
                            self._complete_intent(intent, result)
                            self._after_pass(intent, result)
                            self._current_intent = None
                            break
                if intent is None and self._mission and self._mission_runs < 3 and not self._blocked:
                    # queue dry → follow the title (bounded: 3 mission
                    # passes, then the agent waits for the operator)
                    self._mission_runs += 1
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
                    self._say_state("working", f"following the mission: {self._mission[:100]}")
                    mi = {"id": f"mission-{self._mission_runs}", "text": self._mission, "engagement": self._swarm}
                    result = self._execute_intent(mi)
                    self._complete_intent(mi, result)
                    self._after_pass(mi, result)
                    continue
                if intent is None and not self._blocked and self._state not in ("idle", "pausing", "paused", "waiting"):
                    # alive with nothing to do is a STATE, not "Thinking…"
                    self._say_state("idle", "no work queued — send a message to continue")
                time.sleep(1)  # brief idle between bus checks
                continue

            self._current_intent = intent
            print(f"[{self._identity}] claimed: {intent['text'][:80]}")
            self._say_state("working", f"working: {intent['text'][:100]}")

            # work it
            result = self._execute_intent(intent)

            # report
            self._complete_intent(intent, result)
            self._after_pass(intent, result)
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

    def _pause(self, why: str) -> None:
        """A pause is cooperative: it holds at the next turn boundary. Say that, so "paused" on
        the console is what the agent is doing, not what the console hopes."""
        print(f"[{self._identity}] {why} — holding", flush=True)
        self._paused.set()
        self._say_state("pausing", f"{why} — holds at the next turn")

    def _resume(self) -> None:
        if not self._paused.is_set():
            return
        print(f"[{self._identity}] resumed", flush=True)
        self._paused.clear()
        self._say_state("working", "resumed")

    def _take_note(self, message) -> None:
        """An operator's message. It is read at the next turn — and it revives: a blocked agent,
        or one that had used up its mission passes, starts again."""
        text = str(message or "").strip()[:500]
        if not text:
            return
        print(f"[{self._identity}] message from the operator: {text[:80]}", flush=True)
        # the interjection lands in the worker loop at the next turn boundary
        with self._notes_lock:
            self._operator_notes.append(text)
        held = self._blocked or self._mission_runs >= 3
        self._blocked = False
        self._mission_runs = 0
        self._journal(
            {"engagement": self._swarm},
            [{"kind": "guidance.delivered", "agent": self._identity, "text": text, "ts": _utc_now()}],
        )
        if held:
            self._say_state("working", "message received — starting again")

    def _wait_if_paused(self) -> None:
        """Hold between turns while paused. Stop/delete still work; resume
        clears the hold; a message clears it too — the console promises
        "paused — send a message or Resume to continue"."""
        if not self._paused.is_set():
            return
        print(f"[{self._identity}] paused — holding (resume to continue)", flush=True)
        self._say_state("paused", "paused — holding between turns")
        while self._running and self._paused.is_set():
            time.sleep(0.5)
            for msg in self._bus.drain(20):
                topic = str(msg.get("topic") or "")
                payload = msg.get("payload") or {}
                if not self._targets_me(payload):
                    continue
                if not self._apply_control(topic, payload):
                    return
                if "/cmd/steer" in topic or f"/agent/{self._identity}/steer" in topic:
                    self._resume()

    def _handle_control(self):
        """Process every message on the bus aimed at this agent."""
        for msg in self._bus.drain(20):
            if not self._apply_control(str(msg.get("topic") or ""), msg.get("payload") or {}):
                return

    def _apply_control(self, topic: str, payload: dict) -> bool:
        """One bus message. False when it ends this agent.

        EVERY path drains through here — the main loop, each turn boundary, the pause hold — so a
        message is handled wherever it finds the agent. The turn boundary used to drain the bus
        and keep only stop/pause/resume: an operator's message that arrived mid-turn was dropped
        on the floor (2026-11-28: "send a message" after a pause did nothing)."""
        payload = payload or {}
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
            return False

        # PAUSE: hold between turns (resume clears it). The global pause
        # flag still applies to every agent; a /cmd/pause carries a swarm
        # or agent id so one session does not pause the whole fleet.
        if (event == "pause" or _pause_flag_set()) and self._targets_me(payload):
            self._pause("pause requested")
        if event == "resume" and self._targets_me(payload):
            self._resume()

        if f"/agent/{self._identity}/kill" in topic:
            print(f"[{self._identity}] killed by operator")
            self._running = False
            return False
        # ── the command bus (broadcast control) ─────────────────
        if "/cmd/" in topic:
            if not self._targets_me(payload):
                return True
            cmd = topic.rsplit("/", 1)[-1].lower()
            if cmd in ("stop", "graceful_stop", "shutdown", "delete"):
                print(f"[{self._identity}] command: {cmd} — exiting")
                self._running = False
                return False
            elif cmd == "pause":
                self._pause("pause requested")
            elif cmd == "resume":
                self._resume()
            elif cmd == "steer":
                self._take_note(payload.get("message"))
            elif cmd == "mission":
                new_mission = str(payload.get("mission") or "")[:500]
                if new_mission:
                    self._mission = new_mission
                    self._mission_runs = 0
                    print(f"[{self._identity}] command: mission updated → {new_mission[:60]}")
                    self._say_state("working", f"new mission: {new_mission[:100]}")

        if f"/agent/{self._identity}/steer" in topic:
            self._take_note(payload.get("message"))
        return True

    def _claim_work(self) -> dict | None:
        """The next intent of this swarm — none while blocked: a failure the operator must fix is
        not retried until they speak."""
        return None if self._blocked else self._claim_intent()

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

    def _journal(self, intent: dict, records: list[dict]) -> None:
        """Append to the engagement's events.jsonl and publish on the bus: the console reads the
        file, everything else listens to the bus. Never raises into the loop."""
        if not records:
            return
        # which pass a record belongs to: turn numbers restart at every pass
        records = [{**r, "pass": str((intent or {}).get("id") or "")} for r in records]
        with contextlib.suppress(Exception), self._journal_lock:
            import json as _json

            from suijin.server.platform.lib.workspace import WORKSPACE_DIR

            eng = str((intent or {}).get("engagement") or self._swarm or "default")
            if eng in ("default", ""):
                eng = "_default"  # the workspace's default engagement slug
            ev_dir = Path(WORKSPACE_DIR).parent / "engagements" / eng
            if eng != "_default" and not ev_dir.is_dir():
                # Every spawn creates the engagement's folder before its agent starts. One that is
                # not there any more was DELETED while this agent was mid-call: a write here used
                # to bring it back, and a deleted engagement reappeared as a ghost (2026-11-28).
                # The coordinator's own scratch folder is never deleted and has no spawner.
                return
            ev_dir.mkdir(parents=True, exist_ok=True)
            with (ev_dir / "events.jsonl").open("a") as f:
                for r in records:
                    f.write(_json.dumps(r) + "\n")
            for r in records:
                self._bus.publish(f"/engagement/{eng}/events", r)

    def _say_state(self, state: str, text: str, error: str = "") -> None:
        """Tell the journal — and so the console — what this agent is doing. `error` is also an
        error in the transcript. A repeated state is not repeated."""
        if state == self._state and not error:
            return
        previous, self._state = self._state, state
        records = [
            {
                "kind": "phase.transition",
                "agent": self._identity,
                "from": previous or "starting",
                "to": state,
                "text": str(text)[:400],
                "ts": _utc_now(),
            }
        ]
        if self._model:
            records[0]["model"] = self._model
        if error:
            records.insert(
                0,
                {
                    "kind": "assistant.message",
                    "phase": "error",
                    "agent": self._identity,
                    "content": str(error)[:1200],
                    "ts": _utc_now(),
                },
            )
        self._journal({"engagement": self._swarm}, records)

    def _after_pass(self, intent: dict, result: dict) -> None:
        """Say how a pass ended. The console learns it from the journal, not from this process's
        stdout. A failure the operator must fix is said once and not retried behind their back."""
        from suijin.server.swarm.worker_loop import is_permanent_failure

        state = str((result or {}).get("state") or "done")
        note = " ".join(str((result or {}).get("note") or "").split())
        if (result or {}).get("model"):
            self._model = str(result["model"])
        if state == "done":
            self._say_state("working", f"pass finished — {note[:160]}" if note else "pass finished")
        elif state == "stopped":
            self._say_state("stopped", "stopped by operator")
        elif note.startswith("asked operator:"):
            self._blocked = True
            self._say_state("waiting", note[len("asked operator:") :].strip()[:300])
        elif is_permanent_failure(note):
            self._blocked = True
            self._say_state(
                "blocked",
                "waiting for the operator",
                error=f"Blocked — {note}. Fix the model settings if this is a model error, then send a message to try again.",
            )
        elif state == "exhausted":
            self._blocked = True
            self._say_state(
                "blocked",
                "stopped at the turn cap",
                error=f"Stopped at the turn cap — {note}. Send a message to continue.",
            )
        elif str(intent.get("id") or "").startswith("mission-") and self._mission_runs >= 3:
            self._blocked = True
            self._say_state(
                "blocked",
                "three failed passes — waiting for the operator",
                error=f"Blocked after three failed passes — {note}. Send a message to try again.",
            )
        else:
            self._say_state("retrying", f"pass failed — {note[:200]}", error=f"Agent blocked: {note}")

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

        def _key(turn: int) -> tuple[str, int]:
            return (str(intent.get("id")), turn)

        def _call_id(turn: int, tool_name: str) -> str:
            return f"{self._identity[:14]}-{turn}-{str(tool_name)[:10]}"

        def _on_reasoning(turn: int, text: str) -> None:
            """The model's own reasoning, as it streams."""
            self._reasoned.add(_key(turn))
            self._journal(
                intent,
                [
                    {
                        "kind": "assistant.reasoning",
                        "content": str(text),
                        "agent": self._identity,
                        "seq": turn,
                        "ts": _utc_now(),
                        **({"model": self._model} if self._model else {}),
                    }
                ],
            )

        def _announce(turn: int, thought: str, tool_name: str, tool_args) -> None:
            """The thought and the call, BEFORE the tool runs — once per turn."""
            if _key(turn) in self._announced:
                return
            self._announced.add(_key(turn))
            now = _utc_now()
            records = []
            if thought and _key(turn) not in self._reasoned:
                # The kind stays assistant.message: the terminal UIs read an agent's thought from
                # it. `phase` tells the web console to show it as reasoning.
                records.append(
                    {
                        "kind": "assistant.message",
                        "phase": "thought",
                        "content": str(thought)[:1500],
                        "agent": self._identity,
                        "seq": turn,
                        "ts": now,
                    }
                )
            records.append(
                {
                    "kind": "tool.call",
                    "id": _call_id(turn, tool_name),
                    "name": str(tool_name),
                    "args": tool_args or {},
                    "agent": self._identity,
                    "seq": turn,
                    "ts": now,
                    **({"model": self._model} if self._model else {}),  # known from the first decision on
                }
            )
            self._journal(intent, records)

        def _on_decision(turn: int, thought: str, tool_name: str, tool_args, model: str = "") -> None:
            # A stop that arrived while the model was thinking takes effect BEFORE the tool runs.
            self._handle_control()
            if model:
                self._model = str(model)
            if not self._running:
                print(f"[{self._identity}] stopped mid-mission — before running {tool_name}")
                _kill_child_browsers()  # no orphans, on any exit path
                os._exit(0)
            _announce(turn, thought, tool_name, tool_args)
            with contextlib.suppress(Exception):
                a = dict(tool_args or {})
                hint = f" {{{sorted(a)[0]}: {str(a[sorted(a)[0]])[:40]}}}" if a else ""
            print(f"[{self._identity}] turn {turn}: > {tool_name}{hint}")

        def _on_event(turn: int, tool_name: str, result_str: str, tool_args=None, thought: str = ""):
            # KILL/STOP CHECK PER TURN (field 2026-11-17: control messages
            # only drained at intent boundaries — a title-following mission
            # runs many turns without one, so Delete/Stop left agents
            # running for minutes). The turn boundary is the natural
            # checkpoint: honor every control message, and end on the terminal ones.
            self._handle_control()
            if not self._running:
                print(f"[{self._identity}] stopped mid-mission")
                _kill_child_browsers()  # no orphans, on any exit path
                os._exit(0)
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
            _announce(turn, thought, tool_name, tool_args)
            self._journal(
                intent,
                [
                    {
                        "kind": "tool.result",
                        "id": _call_id(turn, tool_name),
                        "output": " ".join(str(result_str or "").split())[:600],
                        "ok": "error" not in str(result_str or "").lower()[:60],
                        "agent": self._identity,
                        "seq": turn,
                        "ts": _utc_now(),
                    }
                ],
            )
            # THE ACTIVITY STREAM — an operator watching attach/console must
            # see WHAT happened, not a char count (field report: "they are
            # working quickly but I can't see what's actually going on").
            head = " ".join(str(result_str or "").split())[:90]
            mark = "CONFIRMED" if "CONFIRMED" in str(result_str) else ""
            print(f"[{self._identity}] turn {turn}: {tool_name} -> {head}{(' [' + mark + ']') if mark else ''}")

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
            on_decision=_on_decision,
            on_reasoning=_on_reasoning,
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
