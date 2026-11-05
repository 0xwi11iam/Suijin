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
import time

from suijin.server.mesh.bus import BusClient


class SwarmAgent:
    """A headless agent process — claims intents, executes tools, reports."""

    def __init__(self, coordinator_host: str, coordinator_port: int, identity: str = ""):
        self._host = coordinator_host
        self._port = coordinator_port
        self._identity = identity or f"agent-{os.getpid()}"
        self._bus = BusClient()
        self._running = False
        self._current_intent: dict | None = None
        self._iterations = 0

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
                time.sleep(5)  # no work — idle
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
        """Process steering and kill messages."""
        for msg in self._bus.drain(20):
            topic = str(msg.get("topic") or "")
            if f"/agent/{self._identity}/kill" in topic:
                print(f"[{self._identity}] killed by operator")
                self._running = False
                return
            if f"/agent/{self._identity}/steer" in topic:
                payload = msg.get("payload") or {}
                print(f"[{self._identity}] steering: {str(payload.get('message', ''))[:100]}")
                # the steering message rides the next LLM call's context

    def _claim_intent(self) -> dict | None:
        """Request the next intent from the coordinator."""
        # For now: direct database claim (single-machine mode)
        # Future: bus.request("claim_intent", agent_id=self._identity)
        with contextlib.suppress(Exception):
            import sqlite3

            from suijin.server.swarm.coordinator import swarm_db_path

            db = sqlite3.connect(str(swarm_db_path()), timeout=5)
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
                (self._identity, time.time(), iid),
            )
            db.commit()
            db.close()
            return {"id": iid, "text": text, "engagement": engagement}
        return None

    def _execute_intent(self, intent: dict) -> dict:
        """Work one intent using the existing tool pipeline.

        Drives a simple LLM loop: send the intent as the objective,
        receive a tool decision, execute it via dispatch, feed the result
        back, repeat until the model says done or the iteration cap hits.
        The REAL think→execute graph (LangGraph) is heavyweight for the
        swarm agent — this is the lean version with the same tools.
        """
        self._iterations += 1
        print(f"[{self._identity}] working: {intent['text'][:60]}…")

        # the conversation: system directive + intent as objective
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a worker agent on an authorized bug bounty engagement. "
                    "Your ONLY task is the intent below. Work it to completion using "
                    "the available tools. When done, respond with "
                    '{"action": "complete", "reason": "..."} '
                    "along with any findings as facts."
                ),
            },
            {"role": "user", "content": f"INTENT: {intent['text']}\n\nExecute this intent."},
        ]

        max_turns = 10  # lean cap per intent
        facts = []
        state = "done"

        for turn in range(max_turns):
            try:
                from suijin.modules.platform.lib.config_loader import load_config
                from suijin.modules.providers.lib import generate

                cfg = load_config()
                cfg.setdefault("max_tokens_per_request", 4000)
                cfg["intelligence"] = "medium"  # workers don't need max thinking
                response = generate(messages, cfg)
            except Exception as e:  # noqa: BLE001 — provider errors become 'blocked'
                return {"state": "blocked", "facts": facts, "note": f"provider error: {e}"}

            if not isinstance(response, str) or response.startswith("Error:"):
                return {"state": "blocked", "facts": facts, "note": str(response)[:200]}

            # parse the decision
            from suijin.server.agent.lib.decision import normalize_decision

            with contextlib.suppress(Exception):
                import json as _json
                import re as _re

                m = _re.search(r"\{.*\}", response, _re.S)
                if m:
                    decision = normalize_decision(_json.loads(m.group(0)))
                else:
                    decision = {"action": "complete", "reason": response[:200]}
            if not decision:
                decision = {"action": "complete", "reason": "unparseable response"}

            action = str(decision.get("action") or "complete")

            # complete → we're done
            if action == "complete":
                reason = str(decision.get("reason") or decision.get("completion_reason") or "done")
                return {"state": "done", "facts": facts, "note": reason}

            # execute the tool
            tool_name = str(decision.get("tool_name") or "")
            tool_args = decision.get("tool_args") or {}

            if tool_name:
                try:
                    from suijin.modules.tools.lib import dispatch

                    result = dispatch.route_tool(tool_name, tool_args, config={})
                    result_str = str(result)[:2000]
                except Exception as e:  # noqa: BLE001 — tool errors feed back to the LLM
                    result_str = f"Error: {e}"

                # check for findings in the result
                if "CONFIRMED" in result_str:
                    facts.append(
                        {
                            "summary": f"{tool_name} confirmed: {result_str[:150]}",
                            "evidence": result_str[:200],
                        }
                    )

                # feed the result back
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": f"RESULT: {result_str}"})

                # publish the tool call on the bus
                self._bus.publish(
                    f"/engagement/{intent.get('engagement', '?')}/work",
                    {"agent": self._identity, "tool": tool_name, "turn": turn},
                )

        # hit the turn cap — exhausted (budget cut, not done)
        return {"state": "exhausted", "facts": facts, "note": f"hit {max_turns} turn cap"}

    def _complete_intent(self, intent: dict, result: dict):
        """Mark the intent complete and publish any facts."""
        state = result.get("state", "done")
        with contextlib.suppress(Exception):
            import sqlite3

            from suijin.server.swarm.coordinator import swarm_db_path

            db = sqlite3.connect(str(swarm_db_path()), timeout=5)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "UPDATE intents SET state = ?, completed_at = ? WHERE id = ?",
                (state, time.time(), intent["id"]),
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

        print(f"[{self._identity}] {state}: {intent['text'][:60]}")


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
