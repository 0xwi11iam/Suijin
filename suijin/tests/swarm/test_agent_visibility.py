"""What an agent shows the operator, pinned.

Field report 2026-11-28: an engagement's agent failed every model call for nine minutes. Its
journal stayed empty (an agent wrote only when a tool call COMPLETED), the console said "Thinking…"
for as long as the process lived, and the operator could not tell a broken model setting from a
slow thought. Standing rule: surface failures immediately; never leave the interface spinning.

Here: the thought and the call are in the journal before the tool runs, the model's reasoning is
journalled as it streams, a failure is an error in the transcript, a failure the operator must fix
is not retried behind their back, and a message revives a paused or blocked agent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from suijin.server.platform.lib import workspace
from suijin.server.swarm import agent as agent_module
from suijin.server.swarm import worker_loop
from suijin.server.tools.lib import model_routing

SCHEMA = """
CREATE TABLE agents (id TEXT PRIMARY KEY, identity TEXT NOT NULL, joined_at REAL, last_heartbeat REAL,
    current_intent TEXT, status TEXT DEFAULT 'active', swarm TEXT DEFAULT 'default');
CREATE TABLE intents (id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT DEFAULT 'open', agent_id TEXT,
    engagement TEXT, parent_fact TEXT, claimed_at REAL, completed_at REAL, created_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0, swarm TEXT DEFAULT 'default');
CREATE TABLE findings (id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'pending', severity TEXT DEFAULT 'info',
    exchange_ids TEXT, agent_id TEXT, engagement TEXT, created_at REAL);
"""

UNKNOWN_MODEL = (
    'Error: Z.ai API unreachable after 3 attempt(s) — {"error":{"code":"1211",'
    '"message":"Unknown Model, please check the model code."}} [called zai:deepseek/deepseek-v4.1-flash:free]'
)
TIMED_OUT = (
    "provider error: HTTPSConnectionPool(host='api.example.com', port=443): Read timed out [called numes:numes/fast]"
)


class FakeBus:
    def __init__(self) -> None:
        self.inbox: list[dict] = []
        self.published: list[tuple[str, dict]] = []

    def say(self, topic: str, payload: dict) -> None:
        self.inbox.append({"topic": topic, "payload": payload})

    def drain(self, limit: int = 20) -> list[dict]:
        out, self.inbox = self.inbox[:limit], self.inbox[limit:]
        return out

    def publish(self, topic: str, payload: dict) -> bool:
        self.published.append((topic, payload))
        return True

    def connect(self, *_args) -> bool:
        return True

    def subscribe(self, *_args) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """An agent with a fake bus, its own runtime root and its own swarm database."""
    root = tmp_path / ".suijin"
    (root / "config").mkdir(parents=True)
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", root / "config")  # a Path, as in life: runtime_root() reads .name
    db_path = root / "swarm" / "swarm.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(db_path))
    db.executescript(SCHEMA)
    db.execute(
        "INSERT INTO agents (id, identity, last_heartbeat, status, swarm) VALUES ('agent-1-aa', 'agent-1-aa', ?, 'active', 'eng-1')",
        (time.time(),),
    )
    db.commit()
    db.close()
    import suijin.server.swarm.coordinator as coordinator

    monkeypatch.setattr(agent_module, "_swarm_db_path", lambda: db_path)
    monkeypatch.setattr(coordinator, "swarm_db_path", lambda: db_path)
    agent = agent_module.SwarmAgent("127.0.0.1", 1, "agent-1-aa", mission="map the surface", swarm="eng-1")
    agent._bus = FakeBus()
    agent._running = True
    journal = root / "engagements" / "eng-1" / "events.jsonl"
    journal.parent.mkdir(parents=True)  # every spawn creates the engagement's folder before its agent starts

    swarm_agent = agent  # a class body cannot see the fixture's locals under the same name
    journal_file = journal

    class Rig:
        agent = swarm_agent
        bus = swarm_agent._bus
        journal = journal_file

        @staticmethod
        def records() -> list[dict]:
            return [json.loads(line) for line in journal_file.read_text().splitlines()] if journal_file.exists() else []

        @staticmethod
        def transitions() -> list[str]:
            return [str(r.get("to")) for r in Rig.records() if r.get("kind") == "phase.transition"]

        @staticmethod
        def errors() -> list[str]:
            return [str(r.get("content")) for r in Rig.records() if r.get("phase") == "error"]

        @staticmethod
        def delivered() -> list[str]:
            return [str(r.get("text")) for r in Rig.records() if r.get("kind") == "guidance.delivered"]

        @staticmethod
        def idle_loop_stops_after(n: int) -> None:
            """The main loop sleeps between idle checks: end the run after n of them."""
            seen = {"n": 0}

            def fake_sleep(_seconds):
                seen["n"] += 1
                if seen["n"] >= n:
                    agent._running = False

            monkeypatch.setattr(agent_module.time, "sleep", fake_sleep)

    return Rig()


def _drive(rig, monkeypatch, body) -> None:
    """Replace the worker loop with `body`, which drives the callbacks the way the real one does."""

    def fake_execute(
        identity, intent, bus=None, max_turns=0, on_event=None, on_turn=None, on_decision=None, on_reasoning=None
    ):
        return body(intent, on_event, on_decision, on_reasoning)

    monkeypatch.setattr(worker_loop, "execute_intent", fake_execute)
    rig.agent._execute_intent({"id": "mission-1", "text": "map the surface", "engagement": "eng-1"})


def _always(rig, monkeypatch, result: dict) -> None:
    _drive(rig, monkeypatch, lambda intent, on_event, on_decision, on_reasoning: result)


# ── the journal shows work while it happens ──────────────────────────────────────────────────


def test_the_thought_and_the_call_are_in_the_journal_before_the_tool_runs(rig, monkeypatch):
    seen = {}

    def body(intent, on_event, on_decision, on_reasoning):
        on_decision(1, "I will list the inbox first", "list_dir", {"path": "inbox"})
        seen["before the tool ran"] = [r["kind"] for r in rig.records()]
        on_event(1, "list_dir", "a.txt\nb.txt", {"path": "inbox"}, thought="I will list the inbox first")
        return {"state": "done", "facts": [], "note": ""}

    _drive(rig, monkeypatch, body)
    assert seen["before the tool ran"] == ["assistant.message", "tool.call"]
    assert [r["kind"] for r in rig.records()] == ["assistant.message", "tool.call", "tool.result"]  # nothing twice
    thought, call, result = rig.records()
    assert thought["phase"] == "thought" and thought["content"] == "I will list the inbox first"
    assert thought["agent"] == "agent-1-aa"
    assert call["name"] == "list_dir" and call["args"] == {"path": "inbox"}
    assert result["id"] == call["id"] and result["ok"] is True
    # …and the same records travel the bus, for every other listener
    assert [payload.get("kind") for topic, payload in rig.bus.published if str(topic).endswith("/events")] == [
        "assistant.message",
        "tool.call",
        "tool.result",
    ]


def test_the_models_reasoning_is_journalled_as_it_streams(rig, monkeypatch):
    def body(intent, on_event, on_decision, on_reasoning):
        on_reasoning(1, "The surface is a login page; ")
        on_reasoning(1, "the JS bundle decides what is reachable.")
        on_decision(1, "Read the bundle", "http_get", {"url": "https://example.com/app.js"})
        on_event(1, "http_get", "200 OK", {"url": "https://example.com/app.js"}, thought="Read the bundle")
        return {"state": "done", "facts": [], "note": ""}

    _drive(rig, monkeypatch, body)
    records = rig.records()
    assert [r["kind"] for r in records] == ["assistant.reasoning", "assistant.reasoning", "tool.call", "tool.result"]
    assert (
        "".join(r["content"] for r in records[:2])
        == "The surface is a login page; the JS bundle decides what is reachable."
    )
    # the thought is the model's summary of reasoning it already showed: not repeated


def test_a_tool_error_is_still_a_result_the_operator_sees(rig, monkeypatch):
    def body(intent, on_event, on_decision, on_reasoning):
        on_decision(1, "", "http_get", {"url": "https://example.com/"})
        on_event(1, "http_get", "Error: connection refused", {"url": "https://example.com/"})
        return {"state": "done", "facts": [], "note": ""}

    _drive(rig, monkeypatch, body)
    assert [r["kind"] for r in rig.records()] == ["tool.call", "tool.result"]  # no empty thought record
    assert rig.records()[1]["ok"] is False


# ── a failure is said, once ──────────────────────────────────────────────────────────────────


def test_a_failure_the_operator_must_fix_is_shown_and_not_retried_behind_their_back(rig, monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_loop,
        "execute_intent",
        lambda identity, intent, **kwargs: (
            calls.append(intent["id"]) or {"state": "blocked", "facts": [], "note": UNKNOWN_MODEL}
        ),
    )
    rig.idle_loop_stops_after(4)
    rig.agent.run()
    assert calls == ["mission-1"]  # one attempt: retrying a wrong model nine times helps nobody
    assert len(rig.errors()) == 1
    assert "Unknown Model" in rig.errors()[0] and "deepseek/deepseek-v4.1-flash:free" in rig.errors()[0]
    assert "send a message" in rig.errors()[0]  # …and it says how to continue
    assert rig.transitions() == ["working", "blocked"]


def test_a_transient_failure_is_shown_each_time_and_retried_to_the_bound(rig, monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker_loop,
        "execute_intent",
        lambda identity, intent, **kwargs: (
            calls.append(intent["id"]) or {"state": "blocked", "facts": [], "note": TIMED_OUT}
        ),
    )
    rig.idle_loop_stops_after(4)
    rig.agent.run()
    assert calls == ["mission-1", "mission-2", "mission-3"]  # the standing bound: three passes, then it stops
    assert len(rig.errors()) == 3 and all("Read timed out" in error for error in rig.errors())
    assert rig.transitions()[-1] == "blocked"


def test_an_agent_that_ran_out_of_work_says_so_instead_of_looking_busy(rig, monkeypatch):
    _always(rig, monkeypatch, {"state": "done", "facts": [], "note": ""})
    rig.idle_loop_stops_after(4)
    rig.agent.run()
    assert rig.transitions()[-1] == "idle" and rig.errors() == []


# ── messages reach the agent and revive it ───────────────────────────────────────────────────


def test_a_message_while_paused_is_delivered_and_resumes_the_agent(rig):
    rig.agent._paused.set()
    rig.bus.say("/cmd/steer", {"swarm": "eng-1", "message": "skip the login, look at the API first"})
    rig.agent._wait_if_paused()
    assert not rig.agent._paused.is_set()  # "paused — send a message or Resume to continue" is a promise
    assert rig.agent._operator_notes == ["skip the login, look at the API first"]
    assert rig.delivered() == ["skip the login, look at the API first"]
    assert rig.transitions() == ["paused", "working"]


def test_pause_and_resume_are_states_the_console_can_see(rig, monkeypatch):
    holding = threading.Event()

    def fake_sleep(_seconds):
        holding.set()

    monkeypatch.setattr(agent_module.time, "sleep", fake_sleep)
    rig.agent._paused.set()
    holder = threading.Thread(target=rig.agent._wait_if_paused, daemon=True)
    holder.start()
    assert holding.wait(5)
    assert rig.transitions() == ["paused"]  # it holds, and says so
    rig.bus.say("/cmd/resume", {"swarm": "eng-1"})
    holder.join(5)
    assert not holder.is_alive() and not rig.agent._paused.is_set()
    assert rig.transitions() == ["paused", "working"]


def test_a_message_revives_an_agent_that_had_given_up(rig):
    rig.agent._blocked = True
    rig.agent._mission_runs = 3
    rig.bus.say("/cmd/steer", {"swarm": "eng-1", "message": "the worker model is fixed, go again"})
    rig.agent._handle_control()
    assert rig.agent._blocked is False and rig.agent._mission_runs == 0
    assert rig.delivered() == ["the worker model is fixed, go again"]


def test_a_blocked_agent_claims_no_new_work_until_the_operator_speaks(rig):
    db = sqlite3.connect(str(agent_module._swarm_db_path()))
    db.execute(
        "INSERT INTO intents (id, text, state, swarm, created_at) VALUES ('in-1', 'probe the login', 'open', 'eng-1', 1)"
    )
    db.commit()
    db.close()
    rig.agent._blocked = True
    assert rig.agent._claim_work() is None  # the queue has work; the agent waits for the operator
    rig.agent._blocked = False
    assert (rig.agent._claim_work() or {}).get("id") == "in-1"


# ── the real worker loop hands things over in that order ─────────────────────────────────────


@pytest.fixture()
def quiet_loop(tmp_path, monkeypatch):
    """The real execute_intent with everything outside the loop kept out: config, catalog, files, ledger."""
    import suijin.modules.platform.lib.config_loader as config_loader
    import suijin.server.swarm.coordinator as coordinator
    import suijin.server.tools.lib.dead_regions as dead_regions

    monkeypatch.setattr(
        config_loader, "load_config", lambda: {"provider": "zai", "zai_model": "glm-5.3", "numes_model": "deepseek/x"}
    )
    monkeypatch.setattr(config_loader, "load_env", lambda: None)
    monkeypatch.setattr(worker_loop, "_load_catalog", lambda: "ALL AVAILABLE TOOLS\n- probe: a probe")
    monkeypatch.setattr(worker_loop, "_session_hint", lambda: "")
    monkeypatch.setattr(worker_loop, "_hunt_policy", lambda: "")
    monkeypatch.setattr(worker_loop, "_dead_regions_hint", lambda engagement: "")
    monkeypatch.setattr(dead_regions, "ledger_rows", lambda engagement: [])
    monkeypatch.setattr(model_routing, "model_for", lambda task, provider="": "deepseek/x")
    db_path = tmp_path / "swarm.db"
    db = sqlite3.connect(str(db_path))
    db.executescript(SCHEMA)
    db.execute("INSERT INTO agents (id, identity, status) VALUES ('agent-1-aa', 'agent-1-aa', 'active')")
    db.commit()
    db.close()
    monkeypatch.setattr(coordinator, "swarm_db_path", lambda: db_path)
    return db_path


def test_the_worker_loop_streams_reasoning_then_announces_the_call_then_reports_the_result(quiet_loop, monkeypatch):
    import suijin.server.providers.lib as providers
    import suijin.server.tools.lib.dispatch as dispatch

    class Model:
        """One tool call, then a completion the never-done gate challenges before it accepts."""

        def __init__(self):
            self.calls = 0

        def __call__(self, messages, cfg, on_delta=None, **kwargs):
            self.calls += 1
            if on_delta is not None:
                on_delta("reasoning", "the inbox tells me what the operator already collected")
                on_delta("content", '{"thought": "list the inbox"…')  # the reply itself is not shown as reasoning
            if self.calls == 1:
                return '{"thought": "list the inbox", "action": "use_tool", "tool_name": "list_dir", "tool_args": {"path": "inbox"}}'
            return '{"thought": "the surface is mapped", "action": "complete", "reason": "nothing left untested"}'

    model = Model()
    monkeypatch.setattr(providers, "generate", model)
    monkeypatch.setattr(dispatch, "route_tool", lambda tool_name, tool_args, config=None: "a.txt\nb.txt")
    order: list[str] = []

    result = worker_loop.execute_intent(
        "agent-1-aa",
        {"id": "mission-1", "text": "map the surface", "engagement": "eng-1"},
        on_reasoning=lambda turn, text: order.append(f"reasoning: {text}"),
        on_decision=lambda turn, thought, tool_name, tool_args, model="": order.append(
            f"decision: {tool_name} {tool_args} on {model}"
        ),
        on_event=lambda turn, tool_name, result_str, tool_args=None, thought="": order.append(f"result: {result_str}"),
    )
    assert result["state"] == "done"
    assert (
        order[:3]
        == [
            "reasoning: the inbox tells me what the operator already collected",
            "decision: list_dir {'path': 'inbox'} on numes:deepseek/x",  # announced before the tool runs, with the model it thought with
            "result: a.txt\nb.txt",
        ]
    )
    assert result["model"] == "numes:deepseek/x"
    assert model.calls > 2 and order.count("result: a.txt\nb.txt") == 1


def test_a_failed_model_call_says_which_provider_and_model_it_called(quiet_loop, monkeypatch):
    import suijin.server.providers.lib as providers

    monkeypatch.setattr(
        providers,
        "generate",
        lambda messages, cfg, on_delta=None, **kwargs: "Error: Z.ai API unreachable after 3 attempt(s) — unknown model",
    )
    result = worker_loop.execute_intent(
        "agent-1-aa", {"id": "mission-1", "text": "map the surface", "engagement": "eng-1"}
    )
    assert result["state"] == "blocked"
    assert "unknown model" in result["note"]
    assert "[called numes:deepseek/x]" in result["note"]  # the worker route went to the provider that lists the model


def test_a_permanent_failure_is_recognised_from_its_note():
    assert worker_loop.is_permanent_failure(UNKNOWN_MODEL)
    assert worker_loop.is_permanent_failure(
        "provider error: 401 Client Error: Unauthorized for url: https://api.example.com/v1"
    )
    assert worker_loop.is_permanent_failure("Error: Invalid API Key")
    assert not worker_loop.is_permanent_failure(TIMED_OUT)
    assert not worker_loop.is_permanent_failure("model never emitted a JSON decision (5 strikes)")
    assert not worker_loop.is_permanent_failure("asked operator: which host is in scope?")
    assert not worker_loop.is_permanent_failure("")


def test_an_agent_does_not_resurrect_an_engagement_that_was_deleted_under_it(rig, monkeypatch):
    """Delete removes the folder while the agent may still be inside a model call. Its next
    journal write used to re-create the folder, and a deleted engagement came back as a ghost
    on the Engagements page (2026-11-28)."""
    import shutil

    rig.journal.parent.mkdir(parents=True, exist_ok=True)

    def body(intent, on_event, on_decision, on_reasoning):
        on_reasoning(1, "still thinking while the operator deletes this")
        on_decision(1, "", "list_dir", {})
        on_event(1, "list_dir", "gone", {})
        return {"state": "done", "facts": [], "note": ""}

    _drive(rig, monkeypatch, body)
    shutil.rmtree(rig.journal.parent)
    rig.agent._say_state("working", "a state record after the delete")
    rig.agent._journal({"id": "x", "engagement": "eng-1"}, [{"kind": "tool.result", "id": "x"}])
    assert not rig.journal.parent.exists()  # nothing brings it back


def test_the_coordinators_own_scratch_folder_is_still_created_on_demand(rig, monkeypatch):
    agent = agent_module.SwarmAgent("127.0.0.1", 1, "agent-2-bb", mission="m", swarm="")
    agent._bus = FakeBus()
    agent._journal({"engagement": ""}, [{"kind": "phase.transition", "agent": "agent-2-bb", "to": "working"}])
    scratch = rig.journal.parent.parent / "_default" / "events.jsonl"
    assert scratch.exists()  # `_default` is never deleted, and inline agents have nobody to create it
