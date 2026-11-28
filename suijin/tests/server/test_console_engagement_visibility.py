"""An engagement is fully visible — whoever started it: the operator from the landing, or the chat.

Field report 2026-11-28: a chat was asked to organise an engagement. It started one, and the
console showed an empty "Thinking" section over an agent reading "Thinking…" while that agent
failed every model call. The chat stayed under General, apart from the engagement it started; the
session's title was the whole prompt; and the roster had nothing to show, because Suijin sent
every agent with an empty transcript and called "working" anything with a heartbeat.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from suijin.server.platform.lib import workspace
from suijin.server.swarm import lifecycle
from suijin.server.web import console_api

SCHEMA = """
CREATE TABLE agents (id TEXT PRIMARY KEY, identity TEXT NOT NULL, joined_at REAL, last_heartbeat REAL,
    current_intent TEXT, status TEXT DEFAULT 'active', swarm TEXT DEFAULT 'default');
CREATE TABLE intents (id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT DEFAULT 'open', agent_id TEXT,
    engagement TEXT, parent_fact TEXT, claimed_at REAL, completed_at REAL, created_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0, swarm TEXT DEFAULT 'default');
CREATE TABLE findings (id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'pending', severity TEXT DEFAULT 'info',
    exchange_ids TEXT, agent_id TEXT, engagement TEXT, created_at REAL);
"""

LONG_MISSION = (
    "Hunt the login and session handling of example.com: the OAuth redirect handling, the "
    "refresh-token rotation, and the way the API trusts the account header. Build on the two "
    "findings already catalogued, skip anything already reported as a duplicate, and write a note "
    "after every step so the work can be picked up again tomorrow without reading the whole journal."
)
assert len(LONG_MISSION) > 300


def _meta(rt, session: str) -> dict:
    return json.loads((rt.ws / "engagements" / session / "engagement.json").read_text())


def _journal(rt, session: str, records: list[dict]) -> None:
    path = rt.ws / "engagements" / session / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for record in records:
            handle.write(json.dumps({"ts": "2026-11-28T06:00:00Z", **record}) + "\n")


def _agents(rt, rows: list[tuple[str, str]]) -> None:
    """(id, db status) with a fresh heartbeat, all in swarm eng-1."""
    db = sqlite3.connect(str(rt.db))
    db.executemany(
        "INSERT INTO agents (id, identity, last_heartbeat, status, swarm) VALUES (?, ?, ?, ?, 'eng-1')",
        [(agent, agent, time.time(), status) for agent, status in rows],
    )
    db.commit()
    db.close()


def _state(agent: str, to: str, text: str, **extra) -> dict:
    return {"kind": "phase.transition", "agent": agent, "from": "working", "to": to, "text": text, **extra}


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    """The console on its own runtime root, with every mutable registry of its own."""
    ws = tmp_path / ".suijin"
    (ws / "config").mkdir(parents=True)
    (ws / "swarm").mkdir()
    (ws / "engagements").mkdir()
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", ws / "config")
    monkeypatch.setattr(console_api, "_ws", lambda: ws)
    db = sqlite3.connect(str(ws / "swarm" / "swarm.db"))
    db.executescript(SCHEMA)
    db.commit()
    db.close()
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(console_api, "_publish", lambda topic, payload: sent.append((topic, payload)) or True)
    monkeypatch.setattr(console_api, "_bus", lambda: None)
    for name, value in (
        ("_rings", {}),
        ("_seqs", {}),
        ("_event_offsets", {}),
        ("_deleted_sessions", set()),
        ("_deleted_at", {}),
        ("_recent_sessions", {}),
        ("_inflight_hunts", set()),
        ("_chat_running", set()),
        ("_chat_streamed", {}),
    ):
        monkeypatch.setattr(console_api, name, value)
    return SimpleNamespace(ws=ws, db=ws / "swarm" / "swarm.db", sent=sent)


@pytest.fixture()
def no_hunt(rt, monkeypatch):
    """A spawn records what it was asked and does not start anything."""
    hunts: list[dict] = []
    started = threading.Event()

    def fake_hunt(mission, host_n=0, boxed_n=0, swarm=""):
        hunts.append({"mission": mission, "host_n": host_n, "boxed_n": boxed_n, "swarm": swarm})
        started.set()

    monkeypatch.setattr(lifecycle, "run_swarm_hunt", fake_hunt)
    return SimpleNamespace(hunts=hunts, started=started)


# ── the title a sidebar can show, and the mission kept whole ────────────────────────────────


def test_a_title_is_the_first_sentence_or_clause_never_a_wall_of_text():
    short = console_api._short_title
    assert short("map the login flow") == "map the login flow"
    assert short(
        "Can you organise a bug bounty engagement against Example? We already have a few findings, time to push further"
    ) == ("Can you organise a bug bounty engagement against Example?")
    # longer than a sidebar line: its first clause
    assert short(
        "Use the local_sys_info tool, then answer in one short sentence: is everything healthy right now?"
    ) == ("Use the local_sys_info tool")
    assert short(
        "Bug bounty hunt against example.com's public attack surface (example.com web assets, APIs). Build on the findings."
    ) == ("Bug bounty hunt against example.com's public attack surface")
    # no clause to cut at: a word boundary, and never inside a host
    assert short("Review the complete authentication flow of the customer portal and report every weakness", 56) == (
        "Review the complete authentication flow of the customer…"
    )
    assert (
        short("https://example.com/a/very/long/path/without/any/space/in/it/at/all/whatsoever", 32)
        == "https://example.com/a/very/long…"
    )
    assert short("") == "" and short("   \n ") == ""


def test_a_new_session_has_a_short_title_and_keeps_its_whole_text_as_the_mission(rt):
    made = console_api.sessions_create({"title": LONG_MISSION, "kind": "engagement"})["session"]
    assert made["title"] == "Hunt the login and session handling of example.com"
    assert made["targetKey"] == "example.com"
    meta = _meta(rt, made["id"])
    assert meta["description"] == LONG_MISSION and meta["name"] == made["title"]
    assert console_api._mission_with_settings(made["id"]).startswith(LONG_MISSION)  # the agent gets every word


def test_a_chat_title_is_shortened_the_same_way(rt):
    made = console_api.sessions_create(
        {
            "title": "Use the local_sys_info tool, then answer in one short sentence: is everything healthy?",
            "kind": "chat",
        }
    )["session"]
    assert made["title"] == "Use the local_sys_info tool" and made["kind"] == "chat" and made["targetKey"] == ""


def test_a_session_made_before_short_titles_is_shown_short_without_rewriting_its_file(rt):
    for name, description, expected in (
        (
            LONG_MISSION,
            LONG_MISSION,
            "Hunt the login and session handling of example.com",
        ),  # both held the whole prompt
        (
            "Quarterly review notes " + "x" * 90,
            LONG_MISSION,
            "Quarterly review notes " + "x" * 90,
        ),  # renamed by hand: left alone
    ):
        folder = rt.ws / "engagements" / "legacy"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "engagement.json").write_text(json.dumps({"name": name, "description": description, "kind": "chat"}))
        assert console_api._desk_session("legacy")["title"] == expected


# ── the chat that starts an engagement belongs with it ───────────────────────────────────────


def test_the_chat_that_started_an_engagement_files_with_it_and_the_mission_arrives_whole(rt, no_hunt):
    chat = console_api.sessions_create(
        {"title": "Organise a bug bounty engagement for the Example program", "kind": "chat"}
    )["session"]
    assert chat["targetKey"] == ""  # until it is about something

    result = console_api._chat_start_engagement(
        chat["id"], {"mission": LONG_MISSION, "target": "Example program", "mode": "vdp"}
    )
    assert result["ok"] is True and result["path"] == f"/chats/{result['session_id']}"
    assert no_hunt.started.wait(5)
    spawned = no_hunt.hunts[0]
    assert spawned["swarm"] == result["session_id"] and spawned["host_n"] == 1 and spawned["boxed_n"] == 0
    assert LONG_MISSION in spawned["mission"]  # it used to be cut at 200 characters

    engagement = console_api._desk_session(result["session_id"])
    assert engagement["kind"] == "engagement" and engagement["targetLabel"] == "Example program"
    assert engagement["title"] == "Hunt the login and session handling of example.com"
    # the chat sits in the engagement's folder from now on, and remembers what it started
    filing = console_api._desk_session(chat["id"])
    assert filing["kind"] == "chat" and filing["targetKey"] == engagement["targetKey"] == "example program"
    assert _meta(rt, chat["id"])["started"] == [result["session_id"]]
    # the mission is the engagement's first record: on the page now, and after a restart
    with (rt.ws / "engagements" / result["session_id"] / "events.jsonl").open() as handle:
        first = json.loads(handle.readline())
    assert first["kind"] == "user.message" and first["content"] == LONG_MISSION


def test_a_chat_with_no_target_stays_under_general_and_one_about_a_host_files_under_it(rt):
    plain = console_api.sessions_create({"title": "Use the local_sys_info tool once", "kind": "chat"})["session"]
    assert console_api._desk_session(plain["id"])["targetKey"] == ""
    about = console_api.sessions_create(
        {"title": "Explain the error that auth.example.com returns on logout", "kind": "chat"}
    )["session"]
    assert console_api._desk_session(about["id"])["targetKey"] == "auth.example.com"


# ── the roster and the session say what is actually happening ───────────────────────────────


def test_the_roster_shows_each_agents_real_state_and_what_it_is_doing(rt):
    _agents(rt, [("agent-a", "active"), ("agent-b", "active"), ("agent-c", "active"), ("agent-d", "paused")])
    _journal(
        rt,
        "eng-1",
        [
            _state("agent-a", "working", "following the mission: map the surface", model="numes:deepseek/x"),
            # a finished call from the turn before, so a running one can be told apart
            {
                "kind": "tool.call",
                "id": "agent-a-0-list_dir",
                "name": "list_dir",
                "args": {},
                "agent": "agent-a",
                "seq": 0,
                "pass": "mission-1",
            },
            {
                "kind": "tool.result",
                "id": "agent-a-0-list_dir",
                "output": "a.txt",
                "ok": True,
                "agent": "agent-a",
                "seq": 0,
                "pass": "mission-1",
            },
            {
                "kind": "assistant.reasoning",
                "agent": "agent-a",
                "seq": 1,
                "content": "The surface is a login page; ",
                "pass": "mission-1",
            },
            {
                "kind": "assistant.reasoning",
                "agent": "agent-a",
                "seq": 1,
                "content": "the bundle decides what is reachable.",
                "pass": "mission-1",
            },
            {
                "kind": "assistant.message",
                "phase": "thought",
                "agent": "agent-a",
                "seq": 1,
                "content": "Read the bundle",
                "pass": "mission-1",
            },
            {
                "kind": "tool.call",
                "id": "agent-a-1-http_get",
                "name": "http_get",
                "args": {"url": "https://example.com/app.js"},
                "agent": "agent-a",
                "seq": 1,
                "pass": "mission-1",
            },
            _state("agent-b", "working", "following the mission: map the surface"),
            {
                "kind": "assistant.message",
                "phase": "error",
                "agent": "agent-b",
                "content": "Blocked — Unknown Model [called zai:deepseek/x]. Fix the model settings, then send a message to try again.",
            },
            _state("agent-b", "blocked", "waiting for the operator"),
            _state("agent-c", "idle", "no work queued — send a message to continue"),
            _state("agent-d", "working", "following the mission: map the surface"),
            _state("agent-d", "pausing", "pause requested — holds at the next turn"),
            _state("agent-d", "paused", "paused — holding between turns"),
        ],
    )
    roster = {worker["id"]: worker for worker in console_api._worker_list("eng-1")}
    assert {name: worker["status"] for name, worker in roster.items()} == {
        "agent-a": "running",
        "agent-b": "failed",
        "agent-c": "parked",
        "agent-d": "parked",
    }
    working = roster["agent-a"]
    assert working["task"] == "following the mission: map the surface" and working["model"] == "numes:deepseek/x"
    latest = working["transcript"][-1]
    assert latest["assistant"] == "Read the bundle"
    assert latest["reasoning_summary"] == "The surface is a login page; the bundle decides what is reachable."
    assert [(tool["call"]["name"], tool["running"]) for tool in latest["tools"]] == [("http_get", True)]  # in progress
    assert [(tool["call"]["name"], tool["running"], tool["result"]) for tool in working["transcript"][0]["tools"]] == [
        ("list_dir", False, "a.txt")
    ]
    assert roster["agent-b"]["error"].startswith("Blocked — Unknown Model") and roster["agent-b"]["status"] == "failed"
    assert roster["agent-c"]["task"] == "no work queued — send a message to continue"
    assert roster["agent-d"]["task"] == "paused — holding between turns"


def test_the_session_status_follows_the_agents_not_their_heartbeat(rt):
    _agents(rt, [("agent-b", "active"), ("agent-c", "active"), ("agent-d", "paused")])
    _journal(
        rt,
        "eng-1",
        [
            _state("agent-b", "blocked", "waiting for the operator"),
            _state("agent-c", "idle", "no work queued — send a message to continue"),
            _state("agent-d", "paused", "paused — holding between turns"),
        ],
    )
    assert console_api._desk_session("eng-1")["status"] == "idle"  # nobody is working, whatever their heartbeats say
    db = sqlite3.connect(str(rt.db))
    db.execute("DELETE FROM agents WHERE id IN ('agent-b', 'agent-c')")
    db.commit()
    db.close()
    held = console_api._desk_session("eng-1")
    assert held["status"] == "waiting" and held["paused"] is True


def test_an_agent_that_writes_no_state_is_still_taken_at_its_word(rt):
    """An agent on older code, or in its first seconds, writes no state: it is working, as before."""
    _agents(rt, [("agent-old", "active")])
    assert console_api._desk_session("eng-1")["status"] == "working"
    assert console_api._worker_list("eng-1")[0]["status"] == "running"


def test_agent_records_become_events_the_transcript_can_show(rt):
    shaped = console_api._shape_events(
        "eng-1",
        [
            {"kind": "assistant.message", "phase": "thought", "content": "Read the bundle", "agent": "agent-a"},
            {
                "kind": "tool.call",
                "id": "c1",
                "name": "http_get",
                "args": {"url": "https://example.com/a.js"},
                "agent": "agent-a",
            },
            {"kind": "tool.result", "id": "c1", "output": "200 OK", "agent": "agent-a"},
            {"kind": "assistant.message", "phase": "error", "content": "Blocked — Unknown Model", "agent": "agent-a"},
            {"kind": "phase.transition", "to": "blocked", "text": "waiting for the operator", "agent": "agent-a"},
            {"kind": "phase.transition", "to": "working", "text": "resumed", "agent": "agent-a"},  # not worth a line
            {"kind": "phase.transition", "to": "waiting", "text": "which host is in scope?", "agent": "agent-a"},
            {"kind": "guidance.delivered", "text": "the API first", "agent": "agent-a"},
            {"kind": "usage", "tokens": 5},  # not something the transcript shows
        ],
    )
    kinds = [
        (event["type"], event.get("text") or event.get("message") or event["call"].get("id") or event["call"]["name"])
        for event in shaped
    ]
    assert kinds == [
        ("reasoning-delta", "Read the bundle"),  # an agent's thought reads as reasoning, not as its answer
        ("tool-start", "c1"),
        ("tool-result", "c1"),
        ("error", "Blocked — Unknown Model"),
        ("notice", "agent-a is waiting for you — which host is in scope?"),
        ("notice", "message delivered to agent-a"),
    ]


# ── messages ─────────────────────────────────────────────────────────────────────────────────


def test_a_message_to_one_agent_reaches_that_agent_once_and_resumes_it(rt):
    _agents(rt, [("agent-x", "paused"), ("agent-y", "active")])
    console_api.session_message("eng-1", {"text": "skip the login, look at the API first", "workerId": "agent-x"})
    topics = [(topic, payload) for topic, payload in rt.sent]
    assert topics == [
        ("/cmd/resume", {"swarm": "eng-1", "agent": "agent-x"}),
        ("/agent/agent-x/steer", {"swarm": "eng-1", "message": "skip the login, look at the API first"}),
    ]  # not the swarm-wide steer as well: the agent listens to both, and would read it twice
    db = sqlite3.connect(str(rt.db))
    assert dict(db.execute("SELECT id, status FROM agents").fetchall()) == {"agent-x": "active", "agent-y": "active"}
    db.close()


def test_a_message_to_the_session_reaches_every_agent_and_resumes_a_paused_swarm(rt):
    _agents(rt, [("agent-x", "paused")])
    console_api.session_message("eng-1", {"text": "the model is fixed, go again"})
    assert rt.sent == [
        ("/cmd/resume", {"swarm": "eng-1"}),
        ("/cmd/steer", {"swarm": "eng-1", "message": "the model is fixed, go again"}),
    ]


def test_a_message_to_a_made_up_agent_id_goes_to_the_swarm_instead(rt):
    _agents(rt, [("agent-x", "active")])
    console_api.session_message("eng-1", {"text": "hello", "workerId": "../../etc"})
    assert [topic for topic, _payload in rt.sent] == ["/cmd/steer"]


def test_the_first_message_into_a_never_started_engagement_spawns_it_with_the_whole_mission(rt, no_hunt):
    made = console_api.sessions_create({"title": LONG_MISSION, "kind": "engagement"})["session"]
    console_api.session_message(made["id"], {"text": "start with the refresh tokens"})
    assert no_hunt.started.wait(5)
    assert LONG_MISSION in no_hunt.hunts[0]["mission"]
    with (rt.ws / "engagements" / made["id"] / "events.jsonl").open() as handle:
        records = [json.loads(line) for line in handle]
    assert [record["kind"] for record in records] == ["user.message"] and records[0]["content"] == LONG_MISSION


def test_turns_of_a_later_pass_are_not_merged_into_the_first(rt):
    """Turn numbers restart at every pass a title-following agent makes; the pass tells them apart."""
    _agents(rt, [("agent-a", "active")])
    _journal(
        rt,
        "eng-1",
        [
            {
                "kind": "tool.call",
                "id": "agent-a-0-probe",
                "name": "probe",
                "args": {},
                "agent": "agent-a",
                "seq": 0,
                "pass": "mission-1",
            },
            {
                "kind": "tool.result",
                "id": "agent-a-0-probe",
                "output": "first pass",
                "agent": "agent-a",
                "seq": 0,
                "pass": "mission-1",
            },
            {
                "kind": "tool.call",
                "id": "agent-a-0-probe",
                "name": "probe",
                "args": {},
                "agent": "agent-a",
                "seq": 0,
                "pass": "mission-2",
            },
            {
                "kind": "tool.result",
                "id": "agent-a-0-probe",
                "output": "second pass",
                "agent": "agent-a",
                "seq": 0,
                "pass": "mission-2",
            },
        ],
    )
    turns = console_api._worker_list("eng-1")[0]["transcript"]
    assert [turn["tools"][0]["result"] for turn in turns] == ["first pass", "second pass"]


def test_building_the_model_picker_records_which_provider_lists_which_model(rt, monkeypatch, tmp_path):
    """A bare model id in a route goes to the provider that lists it. This is where that is learned."""
    from suijin.server.tools.lib import model_routing

    monkeypatch.setattr(console_api, "_available_models_cache", {"at": 0.0, "models": [], "providers": []})
    monkeypatch.setattr(
        console_api,
        "provider_catalog",
        lambda: {
            "providers": [
                {"key": "numes", "label": "Numes", "configured": True},
                {"key": "zai", "label": "Z.ai", "configured": True},
            ]
        },
    )
    monkeypatch.setattr(console_api, "_provider_model_ids", lambda key: {"numes": ["numes/fast"], "zai": []}[key])
    record = tmp_path / "model-owners.json"
    monkeypatch.setattr(model_routing, "model_owners_path", lambda: record)

    listing = console_api.available_models()
    assert [model["value"] for model in listing["models"]] == ["numes:numes/fast"]  # the route still answers
    assert model_routing.model_owners() == {
        "numes/fast": ["numes"]
    }  # one connection failed: what the other lists is still known
    assert model_routing.owner_of("numes/fast", "zai") == "numes"


def test_a_deleted_engagement_stays_deleted_even_if_something_writes_into_it_a_moment_later(rt, monkeypatch):
    """Seen live (2026-11-28): the agent was inside a model call when Delete landed; its next
    journal write re-created the folder, and the deleted engagement came back as a ghost."""
    made = console_api.sessions_create({"title": "Run the local_sys_info tool once", "kind": "engagement"})["session"]
    folder = rt.ws / "engagements" / made["id"]
    assert (folder / "engagement.json").is_file() and (folder / "reports").is_dir()
    monkeypatch.setattr(console_api, "REPRUNE_DELAYS_S", (0.3, 0.9))
    console_api.session_close(made["id"])
    assert not folder.exists()

    folder.mkdir()  # something still running writes into it a moment later
    (folder / "events.jsonl").write_text('{"kind": "phase.transition", "to": "pausing"}\n')
    time.sleep(1.6)
    assert not folder.exists()  # pruned again, once whatever it was has gone quiet


def test_the_delayed_prune_never_touches_a_live_engagement(rt, monkeypatch):
    made = console_api.sessions_create({"title": "Hunt example.com at leisure", "kind": "engagement"})["session"]
    folder = rt.ws / "engagements" / made["id"]
    console_api._prune_engagement(made["id"])
    time.sleep(0.05)
    assert (folder / "engagement.json").is_file() and (folder / "reports").is_dir()  # metadata present: it is alive


def test_the_same_title_made_again_right_after_a_delete_is_not_swept_by_the_old_delete_s_late_prune(rt, monkeypatch):
    monkeypatch.setattr(console_api, "REPRUNE_DELAYS_S", (0.4, 0.8))
    monkeypatch.setattr(console_api, "_DELETED_STORM_S", 0.2)  # a deleted name is refused only for a moment
    first = console_api.sessions_create({"title": "Look at example.com", "kind": "engagement"})["session"]
    console_api.session_close(first["id"])
    time.sleep(0.3)
    again = console_api.sessions_create({"title": "Look at example.com", "kind": "engagement"})["session"]
    assert again["id"] == first["id"]  # the same words make the same folder
    time.sleep(1.5)
    assert (rt.ws / "engagements" / again["id"] / "engagement.json").is_file()
