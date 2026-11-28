"""Swarm scoping (S1–S3/S5 of the operator's 2026-11-28 plan): an agent claims its own
swarm's lanes first; the autopilot judges each swarm's queue on its own; a swarm with no
work and no live agents is DONE in its definition."""

import json
import sqlite3
import time

import pytest

from suijin.server.swarm import registry
from suijin.server.swarm.coordinator import SwarmCoordinator


@pytest.fixture()
def coord(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "suijin.server.swarm.coordinator.swarm_db_path",
        lambda: tmp_path / "swarm.db",
    )
    monkeypatch.setenv(registry._ROOT_ENV, str(tmp_path / "Targets"))
    c = SwarmCoordinator()
    c.start()
    yield c, tmp_path / "swarm.db"
    c.stop()


def _intent(db: sqlite3.Connection, text: str, swarm: str):
    import hashlib

    iid = "in_" + hashlib.sha1(f"{swarm}:{text}".encode()).hexdigest()[:12]
    db.execute(
        "INSERT OR IGNORE INTO intents (id, text, state, engagement, created_at, swarm) VALUES (?, ?, 'open', '', ?, ?)",
        (iid, text, time.time(), swarm),
    )
    db.commit()
    return iid


def test_an_agent_claims_its_own_swarm_first(coord):
    c, db_path = coord
    db = sqlite3.connect(str(db_path), timeout=5)
    other = _intent(db, "someone else's lane", "immutable")  # older, another swarm
    own = _intent(db, "my own lane", "acme")  # newer, my swarm
    db.execute(
        "INSERT INTO agents (id, identity, last_heartbeat, status, swarm) VALUES ('a1', 'i1', ?, 'active', 'acme')",
        (time.time(),),
    )
    db.commit()
    db.close()
    got = c.claim_next_intent("a1")
    assert got and got["id"] == own, "the agent must take its own swarm's lane, not the older foreign one"
    db = sqlite3.connect(str(db_path), timeout=5)
    state = db.execute("SELECT state, agent_id FROM intents WHERE id = ?", (own,)).fetchone()
    assert state == ("running", "a1")
    db.close()
    _ = other


def test_unswarmed_work_is_claimable_by_anyone(coord):
    c, db_path = coord
    db = sqlite3.connect(str(db_path), timeout=5)
    stray = _intent(db, "queued before the swarm existed", "")
    db.execute(
        "INSERT INTO agents (id, identity, last_heartbeat, status, swarm) VALUES ('a2', 'i2', ?, 'active', 'acme')",
        (time.time(),),
    )
    db.commit()
    db.close()
    got = c.claim_next_intent("a2")
    assert got and got["id"] == stray


def test_autopilot_is_per_swarm(coord, monkeypatch):
    c, db_path = coord
    # swarm acme has an idle agent; another swarm holds work — that must not matter
    c.register_agent("a3", "i3", swarm="acme")
    c.register_agent("a4", "i4", swarm="immutable")
    db = sqlite3.connect(str(db_path), timeout=5)
    db.execute(
        "INSERT INTO intents (id, text, state, swarm, created_at) VALUES ('in_other', 'immutable lane', 'open', 'immutable', ?)",
        (time.time(),),
    )
    db.execute(
        "INSERT INTO facts (id, summary, engagement, created_at) VALUES ('f1', 'acme found a forgotten subdomain', 'acme', ?)",
        (time.time(),),
    )
    db.commit()
    db.close()
    c._last_work_ts = time.time() - 3600  # idle long enough
    c._autopilot_check(time.time())
    db = sqlite3.connect(str(db_path), timeout=5)
    acme_new = db.execute("SELECT COUNT(*) FROM intents WHERE swarm = 'acme' AND state = 'open'").fetchone()[0]
    immutable_new = db.execute("SELECT COUNT(*) FROM intents WHERE swarm = 'immutable' AND state = 'open'").fetchone()[
        0
    ]
    db.close()
    assert acme_new == 1, "the idle swarm's autopilot fires from its own facts"
    assert immutable_new == 1, "a swarm with open work gets nothing new (just its own lane)"


def test_a_spent_swarm_is_marked_done(coord):
    c, db_path = coord
    d = registry.create("Acme", "a finished swarm")
    registry.set_status(d["id"], "running")
    now = time.time()
    # its one agent stopped heartbeating ten minutes ago; no open intents
    db = sqlite3.connect(str(db_path), timeout=5)
    db.execute(
        "INSERT INTO agents (id, identity, last_heartbeat, status, swarm, current_intent) VALUES ('a5', 'i5', ?, 'active', ?, NULL)",
        (now - 600, d["id"]),
    )
    db.commit()
    db.close()
    c._done_last = 0
    c._swarm_done_check(now)
    assert registry.load(d["id"])["status"] == "done"
    # a live agent keeps it running
    d2 = registry.create("Beta", "a live swarm")
    registry.set_status(d2["id"], "running")
    db = sqlite3.connect(str(db_path), timeout=5)
    db.execute(
        "INSERT INTO agents (id, identity, last_heartbeat, status, swarm, current_intent) VALUES ('a6', 'i6', ?, 'active', ?, NULL)",
        (now, d2["id"]),
    )
    db.commit()
    db.close()
    c._done_last = 0
    c._swarm_done_check(now)
    assert registry.load(d2["id"])["status"] == "running"
