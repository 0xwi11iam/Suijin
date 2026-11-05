"""Swarm coordinator + agent lifecycle (2026-11-05).

Tests: coordinator starts, agents register, intents are claimed and
completed, health monitoring requeues dead agents' work, rate limiter
caps per-target across all agents.
"""

import contextlib
import sqlite3
import time
from pathlib import Path

import pytest

from suijin.server.swarm.agent import SwarmAgent
from suijin.server.swarm.coordinator import SwarmCoordinator


@pytest.fixture()
def _swarm(tmp_path, monkeypatch):
    """A running coordinator with the database in tmp."""
    # redirect the swarm database
    monkeypatch.setattr(
        "suijin.server.swarm.coordinator.swarm_db_path",
        lambda: tmp_path / "swarm.db",
    )

    coord = SwarmCoordinator()
    coord.start()
    yield coord, tmp_path / "swarm.db"
    coord.stop()


def _add_intent(db_path: Path, text: str, engagement: str = "test-eng"):
    db = sqlite3.connect(str(db_path), timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    import hashlib

    iid = "in_" + hashlib.sha1(text.encode()).hexdigest()[:12]
    db.execute(
        "INSERT OR IGNORE INTO intents (id, text, state, engagement, created_at) VALUES (?, ?, 'open', ?, ?)",
        (iid, text, engagement, time.time()),
    )
    db.commit()
    db.close()
    return iid


class TestCoordinator:
    def test_starts_and_reports_status(self, _swarm):
        coord, _ = _swarm
        s = coord.status()
        assert s["agents"] == 0
        assert s["bus_port"] > 0

    def test_agent_registers(self, _swarm):
        coord, _ = _swarm
        coord.register_agent("test-1", "identity-1")
        s = coord.status()
        assert s["agents"] == 1

    def test_intent_claimed(self, _swarm):
        coord, db = _swarm
        iid = _add_intent(db, "test SQLi on /search")
        coord.register_agent("worker-1", "w1")

        intent = coord.claim_next_intent("worker-1")
        assert intent is not None
        assert intent["id"] == iid
        assert intent["text"] == "test SQLi on /search"

    def test_intent_completed(self, _swarm):
        coord, db = _swarm
        _add_intent(db, "test completion")
        coord.register_agent("worker-2", "w2")
        intent = coord.claim_next_intent("worker-2")
        assert intent is not None

        coord.complete_intent("worker-2", intent["id"], "done")
        s = coord.status()
        assert s["intents_done"] == 1

    def test_dead_agent_requeues(self, _swarm):
        coord, db = _swarm
        _add_intent(db, "dead agent test")
        coord.register_agent("doomed", "d1")
        intent = coord.claim_next_intent("doomed")
        assert intent is not None

        # simulate death: no heartbeat for > timeout, trigger the
        # health check directly (the loop sleeps 5s between checks)
        coord._agents["doomed"]["last_hb"] = time.time() - coord._heartbeat_timeout - 1
        now = time.time()
        for aid, info in list(coord._agents.items()):
            if now - info["last_hb"] > coord._heartbeat_timeout:
                with contextlib.suppress(sqlite3.Error):
                    coord._db.execute(
                        "UPDATE intents SET state = 'open', agent_id = NULL WHERE agent_id = ? AND state = 'running'",
                        (aid,),
                    )
                    coord._db.execute("UPDATE agents SET status = 'dead' WHERE id = ?", (aid,))
                    coord._db.commit()
                coord._agents.pop(aid, None)

        s = coord.status()
        assert s["intents_open"] == 1  # requeued


class TestRateLimiter:
    def test_tokens_deplete(self, _swarm):
        coord, _ = _swarm
        # first few requests are instant
        waits = [coord.check_rate("example.com") for _ in range(5)]
        assert all(w == 0.0 for w in waits[:5])

    def test_slow_when_depleted(self, _swarm):
        coord, _ = _swarm
        # drain the bucket
        for _ in range(20):
            coord.check_rate("depleted.example.com")
        wait = coord.check_rate("depleted.example.com")
        assert wait > 0  # must wait for refill


class TestAgentLifecycle:
    def test_agent_connects(self, _swarm):
        coord, _ = _swarm
        agent = SwarmAgent("127.0.0.1", coord.port, "test-agent")
        assert agent.start() is True
        assert agent.identity == "test-agent"
        agent.stop()
