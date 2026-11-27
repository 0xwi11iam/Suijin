"""Deleting a chat must leave nothing behind: no queued work, no folder.

Covers the two gaps found by a live end-to-end run (2026-11-27):
  * an intent the session owned under another swarm name stayed ``open`` after Delete — and one
    open intent anywhere pauses the coordinator's idle autopilot;
  * Delete only unlinked ``engagement.json``, so every deleted chat left a folder skeleton.
And the cold-start cause: ``swarm init <title>`` files the title into the DEFAULT swarm even when
the hunt runs in its own swarm.
"""
from __future__ import annotations

import sqlite3
import sys
import types

import pytest

from suijin.server.web import console_api


@pytest.fixture()
def isolated_console(tmp_path, monkeypatch):
    """A console whose workspace is a temp dir, with no bus and no index rebuild."""
    ws = tmp_path / ".suijin"
    (ws / "swarm").mkdir(parents=True)
    (ws / "engagements").mkdir()
    monkeypatch.setattr(console_api, "_ws", lambda: ws)
    monkeypatch.setattr(console_api, "_bus", lambda: None)
    monkeypatch.setattr(console_api, "_bus_addr_direct", lambda: None)
    # The index rebuild reads the operator's real engagement tree: stand the module itself in,
    # so a test can never touch ~/.suijin.
    fake_index = types.ModuleType("suijin.server.platform.lib.engagement_index")
    fake_index.rebuild = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "suijin.server.platform.lib.engagement_index", fake_index)
    return ws


def _swarm_db_with(ws, intents=(), findings=()):
    db = sqlite3.connect(str(ws / "swarm" / "swarm.db"))
    db.execute("CREATE TABLE intents (id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT DEFAULT 'open', agent_id TEXT, engagement TEXT, swarm TEXT DEFAULT 'default')")
    db.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, swarm TEXT DEFAULT 'default', status TEXT DEFAULT 'active', current_intent TEXT, last_heartbeat REAL)")
    db.execute("CREATE TABLE findings (id TEXT PRIMARY KEY, title TEXT, status TEXT, severity TEXT, exchange_ids TEXT, agent_id TEXT, engagement TEXT, created_at REAL)")
    db.execute("CREATE TABLE findings_v2 (id TEXT PRIMARY KEY, program TEXT, engagement TEXT, target TEXT, title TEXT, severity TEXT)")
    for row in intents:
        db.execute("INSERT INTO intents (id, text, state, agent_id, engagement, swarm) VALUES (?, ?, ?, ?, ?, ?)", row)
    for finding_id, engagement in findings:
        db.execute("INSERT INTO findings (id, title, status, severity, exchange_ids, agent_id, engagement, created_at) VALUES (?, 't', 'new', 'low', '', '', ?, 0)", (finding_id, engagement))
    db.commit()
    db.close()


def _intents(ws):
    db = sqlite3.connect(str(ws / "swarm" / "swarm.db"))
    rows = sorted(db.execute("SELECT id, state, engagement, swarm FROM intents").fetchall())
    db.close()
    return rows


# ── session_close ──────────────────────────────────────────────────────────────────────────

def test_delete_removes_every_intent_the_session_owned(isolated_console):
    ws = isolated_console
    session = "demo-session"
    _swarm_db_with(
        ws,
        intents=[
            ("in_own", "own swarm", "open", None, session, session),
            ("in_other_swarm", "queued under the default swarm", "open", None, session, "default"),
            ("in_running", "being worked", "running", "agent-1", session, session),
            ("in_unrelated", "someone else's work", "open", None, "default", "default"),
        ],
    )
    assert console_api.session_close(session) == {"ok": True}
    assert _intents(ws) == [("in_unrelated", "open", "default", "default")]


def test_delete_removes_the_folder_when_nothing_references_the_chat(isolated_console):
    ws = isolated_console
    session = "demo-session"
    eng = ws / "engagements" / session
    (eng / "home" / "tmp").mkdir(parents=True)
    (eng / "reports").mkdir()
    (eng / "events.jsonl").write_text('{"kind":"session.start"}\n', encoding="utf-8")
    (eng / "engagement.json").write_text("{}", encoding="utf-8")
    _swarm_db_with(ws)

    console_api.session_close(session)

    assert not eng.exists(), "a deleted chat with nothing of value must leave no folder"


def test_delete_keeps_the_folder_when_a_finding_points_at_it(isolated_console):
    ws = isolated_console
    session = "demo-session"
    eng = ws / "engagements" / session
    (eng / "home").mkdir(parents=True)
    (eng / "events.jsonl").write_text("{}\n", encoding="utf-8")
    _swarm_db_with(ws, findings=[("f-1", session)])

    console_api.session_close(session)

    assert (eng / "events.jsonl").exists(), "the audit trail a finding points at is evidence"
    assert not (eng / "home").exists(), "the agent sandbox is disposable either way"


def test_delete_keeps_the_folder_when_an_exploit_was_catalogued(isolated_console):
    ws = isolated_console
    session = "demo-session"
    eng = ws / "engagements" / session
    (eng / "exploits" / "EXP-001").mkdir(parents=True)
    (eng / "exploits" / "EXP-001" / "exploit.yaml").write_text("commands: []\n", encoding="utf-8")
    _swarm_db_with(ws)

    console_api.session_close(session)

    assert (eng / "exploits" / "EXP-001" / "exploit.yaml").exists()


def test_delete_never_touches_the_coordinators_own_folder(isolated_console):
    ws = isolated_console
    (ws / "engagements" / "_default" / "home").mkdir(parents=True)
    _swarm_db_with(ws)

    for name in ("default", "", "_default"):
        console_api._prune_engagement(name)

    assert (ws / "engagements" / "_default" / "home").exists()


def test_delete_of_an_unknown_session_is_safe(isolated_console):
    ws = isolated_console
    _swarm_db_with(ws)

    assert console_api.session_close("never-existed") == {"ok": True}
    assert not (ws / "engagements" / "never-existed").exists()


def test_the_delete_route_is_bound_to_session_close():
    """Through the router, not around it: the handler that stops agents must be the one the
    DELETE endpoint calls (a helper inserted under the decorator once took its place)."""
    from fastapi.routing import APIRoute

    deletes = {
        route.path: route.endpoint
        for route in console_api.router.routes
        if isinstance(route, APIRoute) and "DELETE" in (route.methods or set())
    }
    assert deletes.get("/api/console/sessions/{session_id}") is console_api.session_close


# ── cold start ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def lifecycle_db(tmp_path, monkeypatch):
    from suijin.server.swarm import lifecycle

    path = tmp_path / "swarm.db"

    def _db():
        db = sqlite3.connect(str(path), timeout=5)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(lifecycle, "_db", _db)
    db = _db()
    db.execute("CREATE TABLE intents (id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT DEFAULT 'open', agent_id TEXT, engagement TEXT, swarm TEXT DEFAULT 'default')")
    db.commit()
    db.close()
    return lifecycle, path


def _rows(path):
    db = sqlite3.connect(str(path))
    rows = sorted(db.execute("SELECT text, state, swarm FROM intents").fetchall())
    db.close()
    return rows


def test_cold_start_drops_only_the_unclaimed_title_intent_in_the_default_swarm(lifecycle_db):
    lifecycle, path = lifecycle_db
    title = "Map example.test"
    db = sqlite3.connect(str(path))
    db.executemany(
        "INSERT INTO intents (id, text, state, agent_id, engagement, swarm) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("in_title", title, "open", None, "default", "default"),        # the orphan init creates
            ("in_claimed", title, "running", "agent-1", "default", "default"),  # already being worked: not ours to drop
            ("in_own", title, "open", None, "demo-session", "demo-session"),    # the session's own queue: untouched
            ("in_other", "Different work", "open", None, "default", "default"),
        ],
    )
    db.commit()
    db.close()

    assert lifecycle._drop_unclaimed_intent(title, "default") == 1

    assert _rows(path) == [
        ("Different work", "open", "default"),
        ("Map example.test", "open", "demo-session"),
        ("Map example.test", "running", "default"),
    ]


def test_drop_is_a_no_op_when_nothing_matches(lifecycle_db):
    lifecycle, path = lifecycle_db

    assert lifecycle._drop_unclaimed_intent("nothing queued", "default") == 0
