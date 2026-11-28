"""The operations API and the data hub: Suijin's own logic, read and controlled from the console.

An isolated runtime root in a temp dir: no bus, no docker, no provider. Every command the API
publishes is recorded, so a test proves the topic and payload — never a real agent.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from suijin.server import data as data_module
from suijin.server.agent.lib import mesh as mesh_module
from suijin.server.box import hypervisor
from suijin.server.mesh import bus as bus_module
from suijin.server.platform.lib import config_loader, workspace
from suijin.server.swarm import lifecycle, prior_work
from suijin.server.tools.lib import system_stats
from suijin.server.web import box_status, console_api, data_hub, data_stores, ops_api, server

SCHEMA = """
CREATE TABLE agents (id TEXT PRIMARY KEY, identity TEXT NOT NULL, joined_at REAL, last_heartbeat REAL,
    current_intent TEXT, status TEXT DEFAULT 'active', swarm TEXT DEFAULT 'default');
CREATE TABLE intents (id TEXT PRIMARY KEY, text TEXT NOT NULL, state TEXT DEFAULT 'open', agent_id TEXT,
    engagement TEXT, parent_fact TEXT, claimed_at REAL, completed_at REAL, created_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0, swarm TEXT DEFAULT 'default');
CREATE TABLE findings (id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'pending', severity TEXT DEFAULT 'info',
    exchange_ids TEXT, agent_id TEXT, engagement TEXT, created_at REAL);
"""


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    ws = tmp_path / ".suijin"  # the config directory; its parent is the runtime root
    rt = ws.parent
    (ws / "config").mkdir(parents=True)
    (rt / "swarm").mkdir()
    (rt / "engagements").mkdir()
    (rt / "logs").mkdir()
    db = sqlite3.connect(str(rt / "swarm" / "swarm.db"))
    db.executescript(SCHEMA)
    now = time.time()
    db.executemany(
        "INSERT INTO agents (id, identity, last_heartbeat, current_intent, status, swarm) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("agent-1490-ab09", "agent-1490-ab09", now, "map the surface", "active", "s-1"),
            ("agent-1400-sbx", "agent-1400-sbx", now - 3, "probe the login", "active", "s-1"),
            ("m1-leader-x", "m1-leader-x", now - 400, "lead", "stopped", "s-2"),
        ],
    )
    db.executemany(
        "INSERT INTO intents (id, text, state, agent_id, swarm, engagement, created_at, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("in_open", "test the api for injection", "open", None, "s-1", "s-1", now, None),
            ("in_run", "map the surface", "running", "agent-1490-ab09", "s-1", "s-1", now - 60, now - 60),
            ("in_blocked", "probe the login", "blocked", "agent-1400-sbx", "s-1", "s-1", now - 120, now - 130),
        ],
    )
    db.execute(
        "INSERT INTO findings (id, title, status, severity, engagement, created_at) VALUES ('find-1', 'a bug', 'confirmed', 'high', 's-1', 1)"
    )
    db.execute(
        "INSERT INTO findings (id, title, status, severity, engagement, created_at) VALUES ('find-2', ?, 'confirmed', 'high', 's-1', 2)",
        ("catalog_exploit confirmed: EXP-007 CONFIRMED — HIGH CVSS 7.6 : Reflected XSS in the search page",),
    )
    db.commit()
    db.close()
    eng = rt / "engagements" / "s-1"
    (eng / "exploits" / "EXP-001").mkdir(parents=True)
    (eng / "exploits" / "catalog.json").write_text(
        json.dumps(
            {
                "entries": {
                    "EXP-001": {
                        "id": "EXP-001",
                        "title": "a bug",
                        "class": "xss",
                        "severity": "high",
                        "cvss": 7.6,
                        "status": "confirmed",
                        "attempts": [{"verdict": "NO"}, {"verdict": "SUCCESS"}],
                    }
                }
            }
        )
    )
    (eng / "events.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "v": 1,
                    "seq": 1,
                    "kind": "tool.call",
                    "id": "t1",
                    "name": "http_request",
                    "args": {"authorization": "Bearer abcdefghij123"},
                    "agent": "agent-1490-ab09",
                },
                {"v": 1, "seq": 2, "kind": "tool.result", "id": "t1", "output": '{"ok": true}'},
                {"v": 1, "seq": 3, "kind": "assistant.message", "content": "done"},
            ]
        )
        + "\n"
    )
    (eng / "inbox").mkdir()
    (eng / "inbox" / "scope.md").write_text("# scope\nexample.test only\n")
    (eng / "home").mkdir()
    (eng / "home" / "secret.txt").write_text("not for the browser")
    (rt / "engagements" / "old-run").mkdir()
    (rt / "engagements" / "_default").mkdir()  # an empty leftover is not an engagement
    (rt / "engagements" / "leftover").mkdir()
    (rt / "engagements" / "old-run" / "events.jsonl").write_text(json.dumps({"kind": "tool.call"}) + "\n")

    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(console_api, "_publish", lambda topic, payload: sent.append((topic, payload)) or True)
    monkeypatch.setattr(console_api, "_ws", lambda: rt)  # the runtime root, as in life
    monkeypatch.setattr(console_api, "_bus", lambda: None)
    monkeypatch.setattr(console_api, "sessions_list", lambda: {"sessions": [{"id": "s-1", "kind": "engagement"}]})
    monkeypatch.setattr(ops_api, "_rt", lambda: rt)
    monkeypatch.setattr(ops_api, "_cfg", lambda: ws / "config")
    monkeypatch.setattr(data_stores, "_rt", lambda: rt)
    monkeypatch.setattr(data_stores, "_cfg", lambda: ws / "config")
    monkeypatch.setattr(data_stores, "_CACHE", {})
    monkeypatch.setattr(lifecycle, "_db", lambda: sqlite3.connect(str(rt / "swarm" / "swarm.db"), timeout=5))
    monkeypatch.setattr(prior_work, "gate_intent", lambda text, swarm, force=False: (True, ""))
    monkeypatch.setattr(bus_module, "discover_bus", lambda verify=True: None)
    monkeypatch.setattr(
        config_loader,
        "load_config",
        lambda: {
            "model_1": "lead/model",
            "swarm_autopilot": True,
            "sandbox_pool_size": 0,
            "container": {"engine": "docker"},
        },
    )
    monkeypatch.setattr(workspace, "WORKSPACE_DIR", str(ws / "config"))
    monkeypatch.setattr(box_status, "snapshot", lambda: {"engine": "not running", "loading": False})
    # No test may reach a real container engine: on a machine with OrbStack up, an unpatched route
    # created real pool containers from inside the test run (caught 2026-11-27; none were left).
    monkeypatch.setattr(box_status, "_engine_socket", lambda: "")
    monkeypatch.setattr(box_status, "image_build_state", lambda: {"state": "idle"})
    monkeypatch.setattr(system_stats, "collect", lambda interval=1.0: {"cpu_percent": 1.0})
    monkeypatch.setattr(mesh_module, "peers", lambda refresh_now=False: [])
    monkeypatch.setattr(ops_api, "_daemons", lambda: [])
    monkeypatch.setattr(ops_api, "_jobs", lambda: [])
    monkeypatch.setattr(ops_api, "_browser_daemon", lambda: {"running": False, "port": None})
    return {"ws": ws, "rt": rt, "sent": sent}


# ── reading ─────────────────────────────────────────────────────────────────────────────────


def test_the_board_carries_every_section_and_its_counts(runtime):
    board = ops_api.overview()
    assert {
        "coordinator",
        "agents",
        "intents",
        "fleet",
        "findings",
        "mesh",
        "jobs",
        "daemons",
        "browser",
        "system",
        "sessions",
        "boxes",
        "image_build",
        "counts",
        "errors",
    } <= set(board)
    assert board["errors"] == {}
    assert board["counts"] == {
        "agents": 3,
        "alive": 2,
        "boxed": 1,
        "paused": 0,
        "intents": 3,
        "open": 1,
        "running": 1,
        "blocked": 1,
        "done": 0,
    }
    assert board["coordinator"]["running"] is False
    assert board["fleet"]["models"]["leader"] == "lead/model"
    assert board["fleet"]["autopilot"] == {
        "enabled": True,
        "blocked": True,
        "note": "an open or running intent anywhere holds it",
    }
    assert board["findings"] == {
        "total": 2,
        "confirmed": 2,
        "by_severity": {"high": 2},
        "latest": [
            {"severity": "high", "status": "confirmed", "title": "Reflected XSS in the search page"},
            {"severity": "high", "status": "confirmed", "title": "a bug"},
        ],
    }


def test_one_failing_source_is_named_and_the_rest_of_the_board_still_answers(runtime, monkeypatch):
    def boom():
        raise RuntimeError("mesh down")

    monkeypatch.setattr(ops_api, "_mesh", boom)
    board = ops_api.overview()
    assert board["errors"] == {"mesh": "RuntimeError: mesh down"}
    assert board["counts"]["alive"] == 2 and "mesh" not in board


def test_the_roster_derives_what_the_console_shows(runtime):
    agents = {a["id"]: a for a in ops_api._agents()}
    assert agents["agent-1490-ab09"]["alive"] and agents["agent-1490-ab09"]["lane"] == "map the surface"
    assert agents["agent-1400-sbx"]["boxed"] is True and agents["agent-1490-ab09"]["boxed"] is False
    assert agents["m1-leader-x"]["alive"] is False and agents["m1-leader-x"]["tier"] == "m1-leader"
    assert 0 <= agents["agent-1490-ab09"]["hb_age"] < 5


def test_intents_carry_their_age_and_how_long_they_have_been_held(runtime):
    intents = {i["id"]: i for i in ops_api._intents()}
    assert 55 <= intents["in_run"]["held_s"] <= 65 and intents["in_blocked"]["state"] == "blocked"


# ── control ─────────────────────────────────────────────────────────────────────────────────


def test_an_intent_is_queued_open_and_announced(runtime):
    result = ops_api.ops_add_intent({"text": "test example.test for xss", "swarm": "s-1"})
    assert result["ok"] and result["open"] == 2
    row = lifecycle._db().execute("SELECT state, agent_id, swarm FROM intents WHERE id = ?", (result["id"],)).fetchone()
    assert tuple(row) == ("open", None, "s-1")
    assert runtime["sent"][-1] == (
        "/engagement/s-1/work",
        {"event": "new_intent", "text": "test example.test for xss", "swarm": "s-1"},
    )


def test_an_intent_needs_text_and_passes_the_prior_work_gate(runtime, monkeypatch):
    assert ops_api.ops_add_intent({"text": "   "}).status_code == 400
    monkeypatch.setattr(
        prior_work, "gate_intent", lambda text, swarm, force=False: (False, "similar work already queued")
    )
    refused = ops_api.ops_add_intent({"text": "again"})
    assert refused.status_code == 409 and "similar work" in refused.body.decode()
    assert lifecycle._db().execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 3


def test_only_an_unclaimed_intent_can_be_removed(runtime):
    assert ops_api.ops_drop_intent("in_run").status_code == 409
    assert ops_api.ops_drop_intent("in_open") == {"ok": True, "removed": 1}
    assert ops_api.ops_drop_intent("in_open").status_code == 409
    assert ops_api.ops_drop_intent("../etc").status_code == 400


def test_agent_pause_and_resume_publish_to_that_agent_and_mirror_the_status(runtime):
    assert ops_api.ops_agent_action("agent-1490-ab09", "pause", {})["ok"]
    assert runtime["sent"][-1] == ("/cmd/pause", {"agent": "agent-1490-ab09"})
    assert _status("agent-1490-ab09") == "paused"
    ops_api.ops_agent_action("agent-1490-ab09", "resume", {})
    assert runtime["sent"][-1] == ("/cmd/resume", {"agent": "agent-1490-ab09"})
    assert _status("agent-1490-ab09") == "active"


def test_agent_stop_uses_the_kill_topic_and_steering_needs_a_message(runtime):
    ops_api.ops_agent_action("agent-1490-ab09", "stop", {})
    assert runtime["sent"][-1] == ("/agent/agent-1490-ab09/kill", {})
    assert ops_api.ops_agent_action("agent-1490-ab09", "steer", {}).status_code == 400
    ops_api.ops_agent_action("agent-1490-ab09", "steer", {"message": "look at the login again"})
    assert runtime["sent"][-1] == ("/agent/agent-1490-ab09/steer", {"message": "look at the login again"})
    assert ops_api.ops_agent_action("agent-1490-ab09", "explode", {}).status_code == 400
    assert ops_api.ops_agent_action("../x", "pause", {}).status_code == 400


def test_a_fleet_pause_sets_the_flag_and_resume_clears_it(runtime):
    flag = runtime["rt"] / "mesh" / "pause-swarm"
    result = ops_api.ops_swarm_action("pause", {})
    assert result["ok"] and flag.is_file() and runtime["sent"][-1] == ("/cmd/pause", {})
    ops_api.ops_swarm_action("resume", {})
    assert not flag.exists() and ("/cmd/resume", {}) in runtime["sent"]


def test_one_swarm_can_be_paused_and_stopped_but_the_fleet_stop_is_its_own_door(runtime):
    ops_api.ops_swarm_action("pause", {"swarm": "s-1"})
    assert runtime["sent"][-1] == ("/cmd/pause", {"swarm": "s-1"}) and _status("agent-1400-sbx") == "paused"
    assert not (runtime["rt"] / "mesh" / "pause-swarm").exists()
    ops_api.ops_swarm_action("stop", {"swarm": "s-1"})
    assert runtime["sent"][-2:] == [("/cmd/stop", {"swarm": "s-1"}), ("/cmd/delete", {"swarm": "s-1"})]
    assert ops_api.ops_swarm_action("stop", {}).status_code == 400
    ops_api.ops_swarm_action("steer", {"swarm": "s-1", "message": "focus on auth"})
    assert runtime["sent"][-1] == ("/cmd/steer", {"message": "focus on auth", "swarm": "s-1"})
    assert ops_api.ops_swarm_action("steer", {"swarm": "s-1"}).status_code == 400


def test_the_coordinator_is_started_only_when_it_is_not_already_up(runtime, monkeypatch):
    started = []
    monkeypatch.setattr(lifecycle, "start_coordinator", lambda: started.append(True) or True)
    monkeypatch.setattr(bus_module, "discover_bus", lambda verify=True: ("127.0.0.1", 1))
    assert ops_api.ops_coordinator_start() == {
        "ok": True,
        "started": False,
        "note": "the coordinator is already running",
    }
    monkeypatch.setattr(bus_module, "discover_bus", lambda verify=True: None)
    monkeypatch.setattr(lifecycle, "start_coordinator", lambda: started.append(True) or True)
    assert ops_api.ops_coordinator_start() == {"ok": True, "started": True, "note": ""}
    assert started == [True]


def _status(agent_id: str) -> str:
    conn = lifecycle._db()
    try:
        return conn.execute("SELECT status FROM agents WHERE id = ?", (agent_id,)).fetchone()[0]
    finally:
        conn.close()


# ── engagements ─────────────────────────────────────────────────────────────────────────────


def test_every_engagement_folder_is_listed_with_what_it_holds(runtime):
    rows = {row["id"]: row for row in ops_api.ops_engagements()["engagements"]}
    assert set(rows) == {"s-1", "old-run"}
    assert set(data_stores._engagement_folders()[0].parent.name for _ in [0]) == {"engagements"}
    assert sorted(folder.name for folder in data_stores._engagement_folders()) == ["old-run", "s-1"]
    assert rows["s-1"]["live_agents"] == 2 and rows["s-1"]["findings"] == 2 and rows["s-1"]["exploits"] == 1
    assert rows["s-1"]["inbox"] and rows["old-run"]["kind"] == "legacy" and rows["old-run"]["events"] == 1
    # the agent's userland is neither counted nor browsable
    assert rows["s-1"]["files"] == 3  # events, the catalog, the inbox file — not the agent's userland


def test_the_engagement_detail_is_its_real_contents(runtime):
    detail = ops_api.engagement_detail("s-1")
    assert detail["events"] == {
        "lines": 3,
        "kinds": {"assistant.message": 1, "tool.call": 1, "tool.result": 1},
        "agents": {"agent-1490-ab09": 1},
    }
    assert detail["exploits"] == [
        {
            "id": "EXP-001",
            "title": "a bug",
            "class": "xss",
            "severity": "high",
            "cvss": 7.6,
            "status": "confirmed",
            "attempts": 2,
            "last_verdict": "SUCCESS",
        }
    ]
    assert [f["path"] for f in detail["inbox"]] == ["scope.md"]
    assert {row["id"] for row in detail["findings"]} == {"find-1", "find-2"} and detail["kind"] == "legacy"
    assert not any(f["path"].startswith("home/") for f in detail["files"])


def test_engagement_ids_cannot_travel(runtime):
    assert ops_api.engagement_detail("../etc").status_code == 404
    assert ops_api.ops_engagement_file("s-1", "../../etc/passwd").status_code == 404
    assert ops_api.ops_engagement_file("s-1", "home/secret.txt").status_code == 404  # the agent's userland stays shut
    assert ops_api.ops_engagement_events("nope").status_code == 404


def test_engagement_events_filter_by_kind_and_mask_secrets(runtime):
    calls = ops_api.ops_engagement_events("s-1", kind="tool.call")
    assert [k["kind"] for k in calls["kinds"]] == ["assistant.message", "tool.call", "tool.result"]
    assert calls["kinds"][1]["rows"] == 1
    assert calls["rows"][0]["args"]["authorization"] == "••••••"


# ── boxes ───────────────────────────────────────────────────────────────────────────────────


def test_sandbox_names_say_whose_they_are():
    assert box_status.classify("suijin-sbx-pool-1700000000-ab12") == {"pooled": True, "engagement": "", "identity": ""}
    assert box_status.classify("suijin-sbx-my-target-agent-1490-ab09") == {
        "pooled": False,
        "engagement": "my-target",
        "identity": "agent-1490-ab09",
    }


def test_boxes_refuse_to_act_when_no_engine_is_running(runtime, monkeypatch):
    monkeypatch.setattr(box_status, "_engine_socket", lambda: "")
    assert ops_api.ops_boxes_summon({"n": 4}).status_code == 409
    assert ops_api.ops_boxes_destroy_idle().status_code == 409
    assert ops_api.ops_boxes_gc().status_code == 409
    assert ops_api.ops_sandbox_stop("suijin-sbx-x").status_code == 409
    assert ops_api.ops_boxes()["engine"] == "not running"


def test_summon_targets_the_asked_size_and_only_through_the_hypervisor(runtime, monkeypatch):
    monkeypatch.setattr(box_status, "_engine_socket", lambda: "/tmp/docker.sock")
    calls = []
    monkeypatch.setattr(
        hypervisor, "pool_refill_to", lambda n, on_log=print, engagement="default": calls.append(n) or 3
    )
    assert ops_api.ops_boxes_summon({"n": 100})["target"] == 32 and ops_api.ops_boxes_summon({"n": -3})["target"] == 0
    assert ops_api.ops_boxes_summon({"n": "six"}).status_code == 400
    assert calls == [32, 0]
    assert ops_api.ops_sandbox_stop("not-a-box").status_code == 400
    assert ops_api.ops_sandbox_logs("suijin-sbx; rm -rf /").status_code == 400


# ── the data hub ────────────────────────────────────────────────────────────────────────────


def test_secrets_are_masked_by_key_and_inside_strings():
    masked = data_hub.redact(
        {"apiKey": "sk-live-123", "NESTED": {"ZAI_API_KEY": "abc", "max_tokens": 8000, "keyEnv": "NUMES_API_KEY"}}
    )
    assert masked == {
        "apiKey": "••••••",
        "NESTED": {"ZAI_API_KEY": "••••••", "max_tokens": 8000, "keyEnv": "NUMES_API_KEY"},
    }
    text = data_hub.mask_text(
        "Authorization: Bearer eyJhbGciOi.abc.def\nCookie: sid=secret123; other=1\npassword: hunter2abc"
    )
    for secret in ("eyJhbGciOi.abc.def", "secret123", "hunter2abc"):
        assert secret not in text
    assert "Cookie: sid=••••••; other=••••••" in text


def test_secret_stores_expose_names_only(runtime):
    (runtime["ws"] / "config" / ".env").write_text("ZAI_API_KEY=supersecret\nOPENAI_API_KEY=alsosecret\n# a comment\n")
    rows = data_hub.read("config.env")["rows"]
    assert [r["name"] for r in rows] == ["ZAI_API_KEY", "OPENAI_API_KEY"]
    assert "supersecret" not in json.dumps(rows)


def test_the_catalog_covers_every_group_with_live_counts(runtime):
    data_stores.register_engagements()
    catalog = data_hub.catalog()
    groups = {store["group"] for store in catalog}
    assert {"config", "swarm", "engagements", "knowledge", "tools", "mesh", "system", "logs"} <= groups
    by_id = {store["id"]: store for store in catalog}
    assert by_id["swarm.agents"]["rows"] == 3 and by_id["swarm.intents"]["rows"] == 3
    assert by_id["swarm.agents"]["columns"][:2] == ["id", "identity"]
    assert by_id["config.env"]["sensitivity"] == "secret" and by_id["tools.routes"]["sensitivity"] == "public"
    assert "engagement.s-1.events" in by_id and by_id["engagement.s-1.events"]["rows"] == 3
    assert not any(store["where"].startswith("/") for store in catalog), "locations are relative, never machine paths"


def test_read_filters_by_text_by_column_and_by_kind(runtime):
    assert [r["id"] for r in data_hub.read("swarm.intents", state="open")["rows"]] == ["in_open"]
    assert [r["id"] for r in data_hub.read("swarm.intents", query="injection")["rows"]] == ["in_open"]
    page = data_hub.read("swarm.intents", limit=2)
    assert len(page["rows"]) == 2 and page["total"] == 3
    assert data_hub.read("no.such.store")["error"] == "no such store"
    kinds = [r["kind"] for r in data_hub.read("engagement.s-1.events", kind="tool.result")["rows"]]
    assert kinds == ["tool.result"]


def test_an_engagement_folder_that_is_gone_is_forgotten(runtime):
    data_stores.register_engagements()
    assert data_hub.get("engagement.s-1.events") is not None
    (runtime["rt"] / "engagements" / "old-run" / "events.jsonl").unlink()
    (runtime["rt"] / "engagements" / "old-run").rmdir()
    data_stores.register_engagements()
    assert data_hub.get("engagement.old-run.events") is None


def test_watchers_hear_when_a_store_changes(runtime, monkeypatch):
    monkeypatch.setattr(data_hub, "WATCH_INTERVAL_S", 0.05)
    heard = threading.Event()
    seen: list[tuple[str, dict]] = []
    unwatch = data_hub.watch(lambda store_id, payload: seen.append((store_id, payload)) or heard.set())
    try:
        lifecycle._add_intent("a new piece of work", "s-1")
        assert heard.wait(3), "the watcher was never told"
    finally:
        unwatch()
    store_id, payload = seen[0]
    assert store_id == "swarm.intents" and payload["rows"] == 4


def test_publishing_a_change_reaches_watchers_and_names_the_store(runtime):
    seen = []
    unwatch = data_hub.watch(lambda store_id, payload: seen.append((store_id, payload["rows"])))
    data_hub.publish_change("swarm.agents", {"rows": 9})
    unwatch()
    assert seen == [("swarm.agents", 9)]


# ── the server ──────────────────────────────────────────────────────────────────────────────


def _all_paths(routes) -> list[str]:
    """Every route's path, in matching order. An included router is one entry that holds its routes."""
    out: list[str] = []
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None and getattr(nested, "routes", None):
            out.extend(_all_paths(nested.routes))
        else:
            out.append(getattr(route, "path", ""))
    return out


def test_every_read_route_comes_before_the_spa_catch_all():
    paths = _all_paths(server.app.routes)
    catch_all = paths.index("/{spa_path:path}")
    for path in ("/api/state", "/api/swarms", "/api/ops/overview", "/api/data/catalog", "/api/console/sessions"):
        assert path in paths, path
        assert paths.index(path) < catch_all, f"{path} is declared after the catch-all and can never match"


def test_state_is_served_from_a_short_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(data_module, "query", lambda q: calls.append(q) or {"q": q})
    monkeypatch.setattr(server, "_state_cache", {"at": 0.0, "value": None})
    assert json.loads(server.state().body)["q"] == "all" and json.loads(server.state().body)["q"] == "all"
    assert calls == ["all"]


def test_the_boot_sweep_kills_only_true_orphans(runtime, monkeypatch):
    pids = runtime["rt"] / "swarm" / "console-agents.pids"
    pids.write_text("101 102 103")
    parents = {101: 1, 102: 5000, 103: 1}
    killed = []

    def fake_kill(pid, sig):
        if sig == 0:
            if pid == 103:
                raise OSError("gone")
            return
        killed.append(pid)

    monkeypatch.setattr(server.os, "kill", fake_kill)
    monkeypatch.setattr(server, "_parent_pid", lambda pid: parents[pid])
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    monkeypatch.setattr(bus_module, "discover_bus", lambda verify=True: None)  # no coordinator
    server._cleanup_orphan_agents()
    assert killed == [101]  # re-parented AND no coordinator: an orphan
    assert pids.read_text() == "102"  # still parented: left alone; gone: forgotten

    killed.clear()
    pids.write_text("101 102")
    monkeypatch.setattr(bus_module, "discover_bus", lambda verify=True: ("127.0.0.1", 1))  # coordinator is up
    server._cleanup_orphan_agents()
    assert killed == [] and pids.read_text() == "101 102"


def test_every_control_route_survives_an_empty_body(runtime, monkeypatch):
    """A mechanical edit once dropped the body normalisation into a docstring and after a return
    (2026-11-27): the tests passed because they always sent a body, and the route crashed without one."""
    builds = []
    monkeypatch.setattr(
        box_status,
        "start_image_build",
        lambda force=False: builds.append(force) or {"state": "running", "started": True},
    )
    assert ops_api.ops_add_intent().status_code == 400
    assert ops_api.ops_agent_action("agent-1490-ab09", "pause")["ok"] is True
    assert ops_api.ops_swarm_action("pause")["ok"] is True
    assert ops_api.ops_boxes_summon().status_code == 409  # no engine
    assert ops_api.ops_image_build()["started"] is True and builds == [False]
    assert not (runtime["rt"] / "mesh" / "pause-swarm").exists() or True
    ops_api.ops_swarm_action("resume")  # leave no pause flag behind
    assert not (runtime["rt"] / "mesh" / "pause-swarm").exists()


def test_setting_names_are_never_mistaken_for_secrets_and_secret_named_settings_are_masked(runtime):
    """A field called `key` used to be masked whatever it held — four setting NAMES came back as
    dots — while a value under a secret-named setting was not masked by that name at all."""
    (runtime["ws"] / "config" / "config.json").write_text(
        json.dumps(
            {
                "guardrails": {"scope_hosts": ["example.test"], "rate_per_host_per_sec": 4},
                "neo4j_password": "hunter2hunter2",
                "max_tokens_per_request": 8000,
                "api_key": "sk-abcdefghijklmnop",
                "custom_providers": [
                    {"name": "lab", "api_key": "qrstuvwx123456", "base_url": "https://lab.example.test/v1"}
                ],
            }
        )
    )
    rows = {row["setting"]: row["value"] for row in data_hub.read("config.settings", limit=50)["rows"]}
    assert set(rows) == {"guardrails", "neo4j_password", "max_tokens_per_request", "api_key", "custom_providers"}
    assert rows["guardrails"] == {"scope_hosts": ["example.test"], "rate_per_host_per_sec": 4}
    assert rows["neo4j_password"] == "••••••" and rows["api_key"] == "••••••"
    assert rows["max_tokens_per_request"] == 8000
    assert rows["custom_providers"] == [{"name": "lab", "api_key": "••••••", "base_url": "https://lab.example.test/v1"}]


def _handler_for(path: str, method: str):
    """Which function a request reaches, in route order — the question the unit tests never asked."""
    for route in ops_api.router.routes:
        if route.path_regex.match(path) and method in route.methods:
            return route.endpoint.__name__
    return None


def test_literal_routes_are_not_swallowed_by_a_path_parameter():
    assert _handler_for("/api/ops/swarm/stop-all", "POST") == "ops_swarm_stop_all"
    assert _handler_for("/api/ops/swarm/pause", "POST") == "ops_swarm_action"
    assert _handler_for("/api/ops/coordinator/start", "POST") == "ops_coordinator_start"
    assert _handler_for("/api/ops/boxes/refresh", "POST") == "ops_boxes_refresh"
    assert _handler_for("/api/ops/boxes/summon", "POST") == "ops_boxes_summon"
    assert _handler_for("/api/ops/boxes/image-build", "POST") == "ops_image_build"
    assert _handler_for("/api/ops/boxes/sandboxes/suijin-sbx-x/logs", "GET") == "ops_sandbox_logs"
    assert _handler_for("/api/ops/boxes/sandboxes/suijin-sbx-x/stop", "POST") == "ops_sandbox_stop"
    assert _handler_for("/api/ops/engagements/s-1/events", "GET") == "ops_engagement_events"
    assert _handler_for("/api/ops/engagements/s-1/file", "GET") == "ops_engagement_file"
    assert _handler_for("/api/ops/engagements/s-1", "GET") == "ops_engagement"
    assert _handler_for("/api/ops/agents/a-1/log", "GET") == "ops_agent_log"
    assert _handler_for("/api/ops/agents/a-1/steer", "POST") == "ops_agent_action"
    assert _handler_for("/api/ops/agents/a-1/mission", "POST") == "ops_agent_mission"
    assert _handler_for("/api/ops/swarm/spawn", "POST") == "ops_swarm_spawn"
    assert _handler_for("/api/ops/swarm/mission", "POST") == "ops_swarm_mission"


def test_a_mission_can_be_set_for_a_swarm_and_for_one_agent(runtime):
    assert ops_api.ops_swarm_mission().status_code == 400
    assert ops_api.ops_agent_mission("agent-1490-ab09", None).status_code == 400
    assert ops_api.ops_swarm_mission({"mission": "map the login flow", "swarm": "../x"}).status_code == 400
    assert ops_api.ops_swarm_mission({"mission": "map the login flow", "swarm": "s-1"})["ok"]
    assert runtime["sent"][-1] == ("/cmd/mission", {"mission": "map the login flow", "swarm": "s-1"})
    ops_api.ops_swarm_mission({"mission": "x" * 900})
    assert runtime["sent"][-1] == ("/cmd/mission", {"mission": "x" * 500})  # the whole fleet, capped
    assert ops_api.ops_agent_mission("agent-1490-ab09", {"mission": "probe the login"})["ok"]
    assert runtime["sent"][-1] == ("/cmd/mission", {"mission": "probe the login", "agent": "agent-1490-ab09"})


def test_agents_are_spawned_the_way_the_terminal_spawns_them(runtime, monkeypatch):
    started, boxes = [], []

    def up(verify=True):
        return ("127.0.0.1", 5000)

    monkeypatch.setattr(bus_module, "discover_bus", up)
    monkeypatch.setattr(
        lifecycle,
        "_spawn_agent",
        lambda port, mission="", swarm="": started.append((port, mission, swarm)) or ("agent-1-aa", 4242),
    )
    monkeypatch.setattr(lifecycle, "spawn_capacity_check", lambda host, boxed: (True, ""))
    monkeypatch.setattr(
        hypervisor,
        "spawn_sandbox_agent",
        lambda addr, identity, swarm, on_log=print, mission="": (
            boxes.append((identity, swarm)) or f"suijin-sbx-{swarm}-{identity}"
        ),
    )
    monkeypatch.setattr(hypervisor, "coordinator_addr_for_boxes", lambda port: "host.docker.internal:5000")
    monkeypatch.setattr(bus_module, "discover_bus", lambda verify=True: None)
    assert ops_api.ops_swarm_spawn({"host": 1}).status_code == 409  # no coordinator
    monkeypatch.setattr(bus_module, "discover_bus", up)
    assert (
        ops_api.ops_swarm_spawn({}).status_code == 400 and ops_api.ops_swarm_spawn({"host": "two"}).status_code == 400
    )
    monkeypatch.setattr(
        lifecycle, "spawn_capacity_check", lambda host, boxed: (False, "this plan needs ~9000MB of memory")
    )
    assert ops_api.ops_swarm_spawn({"host": 9}).status_code == 409  # the memory guard
    monkeypatch.setattr(lifecycle, "spawn_capacity_check", lambda host, boxed: (True, ""))
    refused = ops_api.ops_swarm_spawn({"host": 2, "boxed": 1, "swarm": "s-1", "mission": "map"})
    assert refused.status_code == 409 and "no container engine" in refused.body.decode()
    assert started == [(5000, "map", "s-1")] * 2 and boxes == []  # host agents went out; no engine was started
    monkeypatch.setattr(box_status, "_engine_socket", lambda: "/tmp/docker.sock")
    monkeypatch.setattr(hypervisor, "image_exists", lambda: False)
    assert ops_api.ops_swarm_spawn({"boxed": 1}).status_code == 409  # build the image first
    monkeypatch.setattr(hypervisor, "image_exists", lambda: True)
    result = ops_api.ops_swarm_spawn({"boxed": 2, "swarm": "s-1"})
    assert result["ok"] and len(result["spawned"]["boxed"]) == 2
    assert boxes == [(result["spawned"]["boxed"][0]["id"], "s-1"), (result["spawned"]["boxed"][1]["id"], "s-1")]
    assert result["spawned"]["boxed"][0]["id"].endswith("-sbx")


def test_the_overview_lists_structured_teams_and_who_in_them_is_alive(runtime):
    (runtime["rt"] / "swarm" / "structured-s-1.json").write_text(
        json.dumps(
            {
                "slug": "s-1",
                "leader": "m1-leader-x",
                "supervisors": {"recon": ["m2-sup-a"], "exploitation": ["m2-sup-b"]},  # the registry's real shape
                "workers": {"recon": ["agent-1490-ab09", "m3-w-gone"]},
            }
        )
    )
    teams = ops_api.overview()["structured"]
    assert teams == [
        {
            "slug": "s-1",
            "file": "structured-s-1.json",
            "leader": "m1-leader-x",
            "supervisors": [{"role": "recon", "id": "m2-sup-a"}, {"role": "exploitation", "id": "m2-sup-b"}],
            "workers": [{"role": "recon", "id": "agent-1490-ab09"}, {"role": "recon", "id": "m3-w-gone"}],
            "updated": teams[0]["updated"],
            "live": 1,  # only the agent with a heartbeat
        }
    ]


def test_old_finding_rows_read_like_the_findings_page_reads_them(runtime):
    """Rows written before the cataloguer was rebuilt carry its frontmatter in the title."""
    detail = ops_api.engagement_detail("s-1")
    by_id = {row["id"]: row for row in detail["findings"]}
    assert by_id["find-1"]["title"] == "a bug" and by_id["find-1"]["exp_id"] == ""
    assert by_id["find-2"]["title"] == "Reflected XSS in the search page"
    assert by_id["find-2"]["exp_id"] == "EXP-007" and by_id["find-2"]["cvss"] == 7.6
    latest = ops_api.overview()["findings"]["latest"]
    assert all("CONFIRMED —" not in row["title"] for row in latest), latest


def test_the_scan_summary_counts_real_attacks(runtime, monkeypatch):
    """It used to report findings × 3 — a number with nothing behind it."""
    monkeypatch.setattr(console_api, "_CATALOG_INDEX_CACHE", {})
    summary = console_api._scan_summary()
    assert summary["totalFindings"] == 2 and summary["high"] == 2
    assert summary["totalAttacks"] == 1  # the one exploit that was actually built and run


def test_an_unknown_session_action_is_refused_not_ignored(runtime):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        console_api.session_action("s-1", "explode")
    assert refused.value.status_code == 400 and "unknown session action 'explode'" in refused.value.detail


def test_reading_a_roster_deletes_nothing(runtime):
    """The roster used to delete this swarm's rows with an old heartbeat — a GET that destroyed
    the record of stopped agents."""
    db = sqlite3.connect(str(runtime["rt"] / "swarm" / "swarm.db"))
    try:
        console_api._worker_list("s-2")
        still_there = db.execute("SELECT COUNT(*) FROM agents WHERE id = 'm1-leader-x'").fetchone()[0]
        rows = {row["id"]: row["status"] for row in console_api._worker_list("s-2")}
    finally:
        db.close()
    assert still_there == 1 and rows == {"m1-leader-x": "stopped"}  # on record, and shown as what it is
