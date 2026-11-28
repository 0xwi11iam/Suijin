"""Chat mode at the console's door: a chat answers and never hunts; an engagement still does.

The turn itself is pinned in test_chat_operator.py. These tests cover what the console adds around
it — the session kind, the records and the event ring, Stop, the operator tools — against a temp
workspace with no bus, no provider and no coordinator.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import types

import pytest

from suijin.server.agent.lib import event_log as event_log_module
from suijin.server.mesh import bus as bus_module
from suijin.server.swarm import lifecycle
from suijin.server.web import console_api

TOOL_STEP = (
    '{"thought": "look it up", "reply": "Checking…", "action": "use_tool", '
    '"tool_name": "suijin_doctor", "tool_args": {}}'
)
FINAL = '{"thought": "read it", "reply": "All good.", "action": "done"}'


@pytest.fixture()
def isolated_console(tmp_path, monkeypatch):
    """A console whose workspace is a temp dir: no bus, no provider, no hunt, no index rebuild."""
    ws = tmp_path / ".suijin"
    (ws / "swarm").mkdir(parents=True)
    (ws / "engagements").mkdir()
    db = sqlite3.connect(str(ws / "swarm" / "swarm.db"))
    db.execute(
        "CREATE TABLE agents (id TEXT PRIMARY KEY, swarm TEXT, status TEXT, current_intent TEXT, last_heartbeat REAL)"
    )
    db.execute(
        "CREATE TABLE intents (id TEXT PRIMARY KEY, text TEXT, title TEXT, state TEXT, created_at REAL, swarm TEXT, updated_at REAL, engagement TEXT, agent_id TEXT)"
    )
    db.execute(
        "CREATE TABLE findings_v2 (id TEXT PRIMARY KEY, program TEXT, engagement TEXT, target TEXT, title TEXT, severity TEXT)"
    )
    db.commit()
    db.close()

    monkeypatch.setattr(console_api, "_ws", lambda: ws)
    monkeypatch.setattr(console_api, "_bus", lambda: None)
    monkeypatch.setattr(console_api, "_bus_addr_direct", lambda: None)
    monkeypatch.setattr(console_api, "_runtime", lambda: {"providerId": "test", "model": "leader"})
    monkeypatch.setattr(console_api, "_chat_tool_listing", lambda: "- suijin_doctor() — Check that Suijin is healthy.")
    monkeypatch.setattr(bus_module, "discover_bus", lambda: None)
    monkeypatch.setattr(event_log_module, "_EVENT_BUS", False)
    monkeypatch.setattr(console_api, "_chat_running", set())
    monkeypatch.setattr(console_api, "_chat_streamed", {})
    for key in (
        "_chat_cancel",
        "_chat_queue",
        "_chat_catalog_cache",
        "_rings",
        "_seqs",
        "_event_offsets",
        "_recent_sessions",
    ):
        monkeypatch.setattr(console_api, key, {})
    monkeypatch.setattr(console_api, "_inflight_hunts", set())
    monkeypatch.setattr(console_api, "_deleted_sessions", set())
    monkeypatch.setattr(console_api, "_deleted_at", {})
    fake_index = types.ModuleType("suijin.server.platform.lib.engagement_index")
    fake_index.rebuild = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "suijin.server.platform.lib.engagement_index", fake_index)

    hunts: list[dict] = []
    published: list[tuple] = []

    def record_hunt(mission, **kwargs):
        hunts.append({"mission": mission, **kwargs})

    monkeypatch.setattr(lifecycle, "run_swarm_hunt", record_hunt)
    monkeypatch.setattr(console_api, "_publish", lambda topic, payload: published.append((topic, payload)) or True)
    monkeypatch.setattr(
        console_api,
        "doctor",
        lambda: {
            "providers": [
                {
                    "id": "p1",
                    "label": "Provider One",
                    "configured": True,
                    "diagnostics": {"valid": True, "reason": "verified", "message": "verified"},
                },
                {
                    "id": "p2",
                    "label": "Provider Two",
                    "configured": True,
                    "diagnostics": {"valid": False, "reason": "failing", "message": "failing (401)"},
                },
                {
                    "id": "p3",
                    "label": "Provider Three",
                    "configured": False,
                    "diagnostics": {"valid": False, "reason": None, "message": "not connected"},
                },
            ],
            "prerequisites": [{"id": "coordinator", "available": False, "required": False}],
        },
    )
    return types.SimpleNamespace(ws=ws, hunts=hunts, published=published)


def _script(monkeypatch, replies, on_call=None):
    """Replace the model with scripted replies; returns the list of message lists it was asked."""
    script = list(replies)
    seen: list[list[dict]] = []

    def generate(session, messages, on_delta):
        seen.append([dict(message) for message in messages])
        if on_call is not None:
            on_call(len(seen))  # the call number: 1 is the first step in flight
        return script.pop(0)

    monkeypatch.setattr(console_api, "_chat_generate", generate)
    return seen


def _settle(slug):
    """Wait until no turn is running for ``slug``. ``_chat_message`` marks a session running
    BEFORE it starts the thread, and a follow-up turn takes the mark over before the previous
    thread returns — so "not running" always means the conversation has settled."""
    deadline = time.time() + 10
    while slug in console_api._chat_running and time.time() < deadline:
        time.sleep(0.005)
    assert slug not in console_api._chat_running, "the chat turn did not finish"


def _chat(title="hello there", **extra):
    created = console_api.sessions_create({"title": title, "kind": "chat", **extra})["session"]
    assert created["kind"] == "chat"
    return created["id"]


def _ring_types(slug):
    return [event["type"] for event in console_api.session_events(slug, after=0)["events"]]


def _record_kinds(slug):
    path = console_api._eng_dir(slug) / "events.jsonl"
    return [json.loads(line)["kind"] for line in path.read_text().splitlines() if line.strip()]


# ── the session kind ────────────────────────────────────────────────────────────────────────


def test_a_chat_session_never_spawns_a_hunt_even_when_asked(isolated_console):
    ws = isolated_console.ws
    slug = _chat(spawn=True)
    assert isolated_console.hunts == []
    meta = json.loads((ws / "engagements" / slug / "engagement.json").read_text())
    assert meta["kind"] == "chat" and meta["target"] == ""
    session = console_api._desk_session(slug)
    assert session["kind"] == "chat" and session["targetKey"] == "" and session["status"] == "idle"


def test_sessions_without_a_kind_are_engagements(isolated_console):
    created = console_api.sessions_create({"title": "recon example.com"})["session"]
    assert created["kind"] == "engagement" and created["targetKey"]
    meta = json.loads((isolated_console.ws / "engagements" / created["id"] / "engagement.json").read_text())
    assert meta["kind"] == "engagement"


def test_a_run_that_is_not_about_a_target_files_under_general_and_stays_listed(isolated_console):
    """Operator 2026-11-27: "when shit isnt related to engagements then its general". A run with no
    target — a tool test, a question — is not about a target: it belongs with the chats, under
    General, and must not vanish from the sidebar the moment its agents stop."""
    off_target = console_api.sessions_create({"title": "Run the local tool local_sys_info once", "kind": "engagement"})[
        "session"
    ]
    on_target = console_api.sessions_create({"title": "Map the attack surface of example.com", "kind": "engagement"})[
        "session"
    ]
    chat = console_api.sessions_create({"title": "what can you do?", "kind": "chat"})["session"]
    assert off_target["targetKey"] == "" and off_target["kind"] == "engagement"
    assert on_target["targetKey"] == "example.com"
    assert chat["targetKey"] == ""
    listed = {s["id"]: s for s in console_api.sessions_list()["sessions"]}
    assert set(listed) >= {off_target["id"], on_target["id"], chat["id"]}, sorted(listed)
    # a no-target run and a chat share the one General group; the targeted one has a folder of its own
    groups = {sid: s["targetKey"] or "general" for sid, s in listed.items()}
    assert groups[off_target["id"]] == "general" and groups[chat["id"]] == "general"
    assert groups[on_target["id"]] == "example.com"


def test_a_legacy_folder_without_a_target_keeps_the_old_listing_rule(isolated_console):
    """Folders that predate the kind field are the 1,000-old-engagement class: still listed only
    while something live shares their target, so the sidebar is not flooded with them."""
    legacy = isolated_console.ws / "engagements" / "20260101_000000_old-run"
    legacy.mkdir()
    (legacy / "engagement.json").write_text(json.dumps({"name": "old run with no target", "target": ""}))
    assert legacy.name not in [s["id"] for s in console_api.sessions_list()["sessions"]]


# ── a conversation turn ─────────────────────────────────────────────────────────────────────


def test_a_message_into_a_chat_runs_a_conversation_turn(isolated_console, monkeypatch):
    slug = _chat()
    working_when_asked = []
    _script(
        monkeypatch,
        [TOOL_STEP, FINAL],
        on_call=lambda n: working_when_asked.append(console_api._desk_session(slug)["status"]),
    )
    console_api.session_message(slug, {"text": "are you healthy?"})
    _settle(slug)

    assert working_when_asked == ["working", "working"]
    assert console_api._desk_session(slug)["status"] == "idle"
    assert isolated_console.hunts == []
    # nothing was steered, paused or resumed on the bus — there are no agents to steer
    assert isolated_console.published == []

    assert _record_kinds(slug) == [
        "user.message",
        "assistant.reasoning",
        "assistant.message",
        "tool.call",
        "tool.result",
        "assistant.reasoning",
        "assistant.message",
    ]
    assert _ring_types(slug) == [
        "user",
        "reasoning-delta",
        "assistant-delta",
        "tool-start",
        "tool-result",
        "reasoning-delta",
        "assistant-delta",
    ]
    # the poller reads the same records back from disk and must not ring them a second time
    assert _ring_types(slug).count("assistant-delta") == 2


def test_the_snapshot_rebuilds_the_conversation_including_the_reasoning(isolated_console, monkeypatch):
    slug = _chat()
    _script(monkeypatch, [TOOL_STEP, FINAL])
    console_api.session_message(slug, {"text": "are you healthy?"})
    _settle(slug)
    messages = console_api.session_snapshot(slug)["snapshot"]["messages"]
    assert messages[0]["role"] == "user" and messages[0]["content"][0]["text"] == "are you healthy?"
    assert {"type": "reasoning", "text": "look it up"} in messages[1]["content"]
    tool_use, tool_result = messages[3]["content"][0], messages[4]["content"][0]
    assert tool_use["type"] == "tool_use" and tool_use["name"] == "suijin_doctor" and tool_use["input"] == {}
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == tool_use["id"]
    assert messages[-1]["content"] == [{"type": "text", "text": "All good."}]


def test_the_model_is_asked_on_the_leader_with_the_thread_and_the_operator_tools(isolated_console, monkeypatch):
    slug = _chat()
    seen = _script(monkeypatch, [FINAL])
    console_api.session_message(slug, {"text": "what can you do?"})
    _settle(slug)
    system = seen[0][0]
    assert system["role"] == "system"
    for needle in ("start_engagement", "suijin_doctor", "Never start probing", '"action"'):
        assert needle in system["content"], needle
    assert seen[0][-1] == {"role": "user", "content": "what can you do?"}


def test_a_second_turn_sees_the_first(isolated_console, monkeypatch):
    slug = _chat()
    seen = _script(monkeypatch, [TOOL_STEP, FINAL, FINAL])
    console_api.session_message(slug, {"text": "first?"})
    _settle(slug)
    console_api.session_message(slug, {"text": "second?"})
    _settle(slug)
    roles = [(message["role"], message["content"][:24]) for message in seen[-1][1:]]
    assert roles[0] == ("user", "first?")
    assert ("assistant", "All good.") in [(r, c) for r, c in roles]
    assert roles[-1] == ("user", "second?")


# ── Stop, queue, delete ─────────────────────────────────────────────────────────────────────


def test_stop_ends_the_turn_after_its_current_step_and_touches_no_agent(isolated_console, monkeypatch):
    slug = _chat()

    def stop_during_the_first_call(_n):
        console_api.session_action(slug, "pause", {})

    seen = _script(monkeypatch, [TOOL_STEP, FINAL], on_call=stop_during_the_first_call)
    console_api.session_message(slug, {"text": "go on"})
    _settle(slug)
    assert len(seen) == 1  # the model was never asked for a second step
    assert _record_kinds(slug)[-1] == "tool.result"
    assert console_api._desk_session(slug)["status"] == "idle"
    assert [topic for topic, _ in isolated_console.published] == []
    notices = [
        event["text"] for event in console_api.session_events(slug, after=0)["events"] if event["type"] == "notice"
    ]
    assert notices == ["stopped — send a message to continue"]


def test_a_message_sent_while_the_turn_works_is_read_before_the_next_step(isolated_console, monkeypatch):
    slug = _chat()

    def send_during_the_first_call(call):
        if call == 1:  # once, while the first step is in flight
            console_api.session_message(slug, {"text": "and the findings?"})

    seen = _script(monkeypatch, [TOOL_STEP, FINAL], on_call=send_during_the_first_call)
    console_api.session_message(slug, {"text": "what is running?"})
    _settle(slug)
    assert len(seen) == 2  # one turn answered both — no second turn was started
    last_user = [m for m in seen[1] if m["role"] == "user"][-1]
    assert "and the findings?" in last_user["content"]
    assert _record_kinds(slug).count("user.message") == 2
    assert threading.active_count() == 1 or not any(t.name == f"chat-{slug}" for t in threading.enumerate())


def test_a_model_failure_is_shown_to_the_operator_and_the_session_goes_idle(isolated_console, monkeypatch):
    slug = _chat()
    _script(monkeypatch, ["Error: 429 rate limited"])
    console_api.session_message(slug, {"text": "hi"})
    _settle(slug)
    events = console_api.session_events(slug, after=0)["events"]
    assert events[-1]["type"] == "error" and "429" in events[-1]["message"]
    assert console_api._desk_session(slug)["status"] == "idle"


def test_delete_forgets_the_chat(isolated_console, monkeypatch):
    slug = _chat()
    _script(monkeypatch, [FINAL])
    console_api.session_message(slug, {"text": "hi"})
    _settle(slug)
    assert console_api._chat_streamed.get(slug)
    console_api.session_close(slug)
    assert slug not in console_api._chat_streamed and slug not in console_api._chat_queue


def test_a_deleted_name_can_be_used_again(isolated_console, monkeypatch):
    """Found while re-running a journey (2026-11-27): a deleted name stayed in the resurrect guard
    forever, so sending the same first message again made a session that was never listed — and,
    for an engagement, never started."""
    first = _chat("hello again")
    console_api.session_close(first)
    assert first in console_api._deleted_sessions

    # the delete's own request storm: a create inside the window is refused, and nothing half-dead is made
    refused = console_api.sessions_create({"title": "hello again", "kind": "chat"})
    assert getattr(refused, "status_code", None) == 409  # as an HTTP route this is the 409 the console shows
    assert not console_api._eng_dir(first).exists()

    # once the storm has passed, the same name is a normal new chat
    monkeypatch.setattr(console_api, "_deleted_at", {first: time.time() - 60})
    again = console_api.sessions_create({"title": "hello again", "kind": "chat"})["session"]
    assert again["id"] == first and first not in console_api._deleted_sessions
    assert first in [session["id"] for session in console_api.sessions_list()["sessions"]]
    _script(monkeypatch, [FINAL])
    console_api.session_message(first, {"text": "hi"})
    _settle(first)
    assert _record_kinds(first)[-1] == "assistant.message"


# ── the operator tools ──────────────────────────────────────────────────────────────────────


def test_start_engagement_creates_an_engagement_that_hunts(isolated_console):
    result = console_api._chat_tool(
        "some-chat",
        "start_engagement",
        {"mission": "Recon example.com, read-only", "target": "example.com", "permissions": "recon", "mode": "vdp"},
    )
    assert result["ok"] is True and result["path"] == f"/chats/{result['session_id']}"
    assert len(isolated_console.hunts) == 1
    new = console_api._desk_session(result["session_id"])
    assert new["kind"] == "engagement" and new["targetKey"] == "example.com"
    meta = json.loads((isolated_console.ws / "engagements" / result["session_id"] / "engagement.json").read_text())
    assert meta["permissions"] == "recon" and meta["mode"] == "vdp"


def test_start_engagement_refuses_what_it_cannot_run(isolated_console):
    assert console_api._chat_tool("c", "start_engagement", {"mission": "  "}).startswith(
        "Error: start_engagement needs a mission"
    )
    pentest = console_api._chat_tool("c", "start_engagement", {"mission": "test everything", "mode": "pentest"})
    assert pentest.startswith("Error:") and "scope document" in pentest
    assert isolated_console.hunts == []


def test_doctor_summarises_the_health_check(isolated_console):
    result = console_api._chat_tool("c", "suijin_doctor", {})
    assert result["ok"] is False
    assert result["verified"] == ["Provider One"] and result["failing"] == ["Provider Two"]
    assert [p["provider"] for p in result["providers"]] == ["Provider One", "Provider Two"]  # only the ones with a key
    assert result["coordinator_running"] is False
    assert result["summary"] == "1 verified, 1 failing, 0 saved but untested; coordinator not running"


def test_any_other_tool_goes_through_the_dispatch_door(isolated_console, monkeypatch):
    from suijin.modules.tools.lib import dispatch

    calls = []
    monkeypatch.setattr(
        dispatch, "route_tool", lambda name, args, config=None: calls.append((name, args, config)) or "tool output"
    )
    assert console_api._chat_tool("c", "mesh_status", {"a": 1}) == "tool output"
    assert calls == [("mesh_status", {"a": 1}, {})]


# ── the prompt's facts ──────────────────────────────────────────────────────────────────────


def test_the_state_note_reports_sessions_findings_and_attached_files(isolated_console, monkeypatch):
    slug = _chat("first chat")
    db = sqlite3.connect(str(isolated_console.ws / "swarm" / "swarm.db"))
    db.executemany(
        "INSERT INTO findings_v2 (id, severity) VALUES (?, ?)", [("f1", "high"), ("f2", "medium"), ("f3", "medium")]
    )
    db.commit()
    db.close()
    note = console_api._chat_state_note(slug)
    assert "test/leader" in note
    assert "- engagements: none" in note
    assert "- chats (this one included): 1" in note
    assert "- findings catalogued: 3 (medium 2, high 1)" in note
    assert "attached" not in note

    # a file attached on the landing is recorded the way the upload route records it
    inbox = isolated_console.ws / "engagements" / slug / "inbox"
    inbox.mkdir()
    (inbox / "scope.md").write_text("# scope")
    meta_path = isolated_console.ws / "engagements" / slug / "engagement.json"
    meta = json.loads(meta_path.read_text())
    meta.update(files=["scope.md"], inbox=str(inbox))
    meta_path.write_text(json.dumps(meta))
    note = console_api._chat_state_note(slug)
    assert f"- files the operator attached to this chat (in {inbox}; read them with read_file): scope.md" in note


def test_the_tool_listing_is_the_plain_list_without_the_hunt_doctrine(monkeypatch):
    from suijin.modules.tools.lib import dispatch

    catalog = (
        "## HOW TO CALL ANY TOOL (read this first)\n"
        "##  MANDATORY TOOLS — Use These Every Turn\n- **write_note** — MANDATORY after EVERY action\n"
        "## ALL AVAILABLE TOOLS (complete — one line each)\n- http_request(method, url) — Make an HTTP request.\n"
        "## PACK GUIDES\nstuff"
    )
    monkeypatch.setattr(dispatch, "get_tool_catalog", lambda: catalog)
    monkeypatch.setattr(console_api, "_chat_catalog_cache", {"at": 0.0, "text": ""})
    listing = console_api._chat_tool_listing()
    assert listing.startswith("## ALL AVAILABLE TOOLS") and "http_request(method, url)" in listing
    assert "MANDATORY" not in listing and "PACK GUIDES" not in listing
    assert console_api._chat_tool_listing() is listing  # cached


def test_engagements_still_lazy_spawn_their_hunt(isolated_console, monkeypatch):
    created = console_api.sessions_create({"title": "recon example.com"})["session"]
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)
    console_api.session_message(created["id"], {"text": "go"})
    for _ in range(50):
        if isolated_console.hunts:
            break
        threading.Event().wait(0.02)
    assert len(isolated_console.hunts) == 1 and "recon example.com" in isolated_console.hunts[0]["mission"]


# ── delete while answering ──────────────────────────────────────────────────────────────────


def test_a_deleted_chat_stops_outputting_even_from_the_call_it_was_in(isolated_console, monkeypatch):
    """Seen live (2026-11-28): a chat was deleted while it was answering; its turn, still inside the
    model call, went on emitting into a fresh ring under the same session id — and the next chat of
    that name opened on the dead chat's reasoning and reply."""
    release, entered = threading.Event(), threading.Event()

    def generate(session, messages, on_delta):
        entered.set()
        release.wait(5)  # a model call still in flight when Delete lands
        if on_delta:
            on_delta("reasoning", "the deleted chat is still thinking")
        return FINAL

    monkeypatch.setattr(console_api, "_chat_generate", generate)
    first = console_api.sessions_create({"title": "Same first message", "kind": "chat"})["session"]["id"]
    console_api.session_message(first, {"text": "Same first message"})
    assert entered.wait(5)
    console_api.session_close(first)
    assert first not in console_api._rings
    release.set()
    _settle(first)
    time.sleep(0.2)
    assert first not in console_api._rings and first not in console_api._seqs  # nothing refilled them
    assert not (isolated_console.ws / "engagements" / first).exists()  # …nor its folder


def test_a_new_chat_never_inherits_the_events_of_a_deleted_one_of_the_same_name(isolated_console, monkeypatch):
    """Whatever left a ring behind under a name, a session made anew starts clean."""
    from collections import deque

    console_api._rings["same-first-message"] = deque(
        [{"type": "reasoning-delta", "sequence": 1, "text": "a dead chat's thought"}], maxlen=console_api.MAX_RING
    )
    console_api._seqs["same-first-message"] = 1
    _script(monkeypatch, [FINAL])
    made = console_api.sessions_create({"title": "Same first message", "kind": "chat"})["session"]
    assert made["id"] == "same-first-message"
    assert console_api.session_snapshot(made["id"])["snapshot"]["events"] == []  # nothing of the old one
    console_api.session_message(made["id"], {"text": "Same first message"})
    _settle(made["id"])
    kinds = [event["type"] for event in console_api.session_snapshot(made["id"])["snapshot"]["events"]]
    assert kinds[0] == "user" and "a dead chat's thought" not in str(list(console_api._rings[made["id"]]))


def test_a_deleted_turn_winding_down_does_not_tear_down_the_chat_made_again_under_its_name(
    isolated_console, monkeypatch
):
    """The same race, the other half: the old turn's closing block used to clear the session's
    "running" mark and cancel flag — the new chat's, by then."""
    release, entered = threading.Event(), threading.Event()

    def first_generate(session, messages, on_delta):
        entered.set()
        release.wait(5)
        return FINAL

    monkeypatch.setattr(console_api, "_chat_generate", first_generate)
    first = console_api.sessions_create({"title": "Same first message", "kind": "chat"})["session"]["id"]
    console_api.session_message(first, {"text": "Same first message"})
    assert entered.wait(5)
    console_api.session_close(first)
    console_api._deleted_sessions.discard(first)  # the guard has expired
    console_api._recent_sessions.pop(first, None)
    monkeypatch.setattr(console_api, "_chat_generate", lambda session, messages, on_delta: FINAL)
    second = console_api.sessions_create({"title": "Same first message", "kind": "chat"})["session"]["id"]
    assert second == first  # the same words make the same session
    console_api.session_message(second, {"text": "Same first message"})
    release.set()  # the deleted turn now winds down, after the new one started
    _settle(second)
    kinds = _record_kinds(second)
    assert kinds[0] == "user.message" and kinds[-1] == "assistant.message", kinds
    events = [event["type"] for event in console_api.session_snapshot(second)["snapshot"]["events"]]
    assert events.count("user") == 1 and "assistant-delta" in events, events


# ── the reply survives broken JSON ──────────────────────────────────────────────────────────


def test_a_broken_decision_still_yields_the_reply():
    """Seen live 2026-11-28: the model left an unescaped quote inside its thought, the strict
    parse refused the whole object, and the operator's answer rendered as one raw JSON blob."""
    from suijin.server.web.chat_operator import parse_step

    blob = (
        '{"thThe state block already told me: no engagements.", '
        '"reply": "Nothing is running right now:\\n\\n**Engagements**\\n- none active", '
        '"action": "done"}'
    )
    step = parse_step(blob)
    assert step["action"] == "done"
    assert step["reply"].startswith("Nothing is running right now:")
    assert "**Engagements**" in step["reply"] and "\n" in step["reply"]


def test_prose_stays_prose_and_tools_survive_salvage():
    from suijin.server.web.chat_operator import parse_step

    prose = parse_step("Just a plain sentence, no JSON.")
    assert prose["reply"] == "Just a plain sentence, no JSON." and prose["action"] == "done"
    tool = parse_step(
        '{"thBroken "quote here", "reply": "Checking the swarm…", '
        '"action": "use_tool", "tool_name": "local_sys_info", "tool_args": {}}'
    )
    assert tool["action"] == "use_tool" and tool["tool_name"] == "local_sys_info"
    assert tool["reply"] == "Checking the swarm…"
