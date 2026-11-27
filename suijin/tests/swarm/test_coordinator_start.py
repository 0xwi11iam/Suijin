"""Starting the swarm coordinator must always run THIS tree's code, from every entry point.

Found while verifying the landing end to end (2026-11-27): the hunt's cold start shelled out to the
``suijin`` launcher on PATH — a pip-installed copy — so the first chat after a cold start ran its
coordinator on whatever version was installed, not on the tree the console runs from. That launcher
is also what filed the hunt's title into the DEFAULT swarm's intent queue. The dashboard's Start
Engine had its own fault: ``python -m suijin.server.swarm.coordinator`` imports a module with no
``__main__`` block and exits at once.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from suijin.server.swarm import lifecycle

ENTRY = "from suijin.server.swarm.coordinator import run_swarm_start; run_swarm_start()"


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    """A runtime root in a temp dir; no process is really started and no time is really waited."""
    calls: dict[str, list] = {"popen": [], "run": []}
    state: dict[str, object] = {"bus": None, "schema": True}

    monkeypatch.setattr(lifecycle, "_swarm_db", lambda: tmp_path / ".suijin" / "swarm" / "swarm.db")
    monkeypatch.setattr(lifecycle, "_bus_addr", lambda: state["bus"])
    monkeypatch.setattr(lifecycle, "_wait_for_schema", lambda timeout=15.0: state["schema"])
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)

    class Handle:
        pass

    def popen(argv, **kwargs):
        calls["popen"].append((argv, kwargs))
        if state["bus"] == "after-start":
            state["bus"] = ("127.0.0.1", 40000)
        return Handle()

    def run(argv, *args, **kwargs):
        calls["run"].append(argv)
        raise AssertionError(f"nothing may shell out during these tests, got: {argv}")

    monkeypatch.setattr(lifecycle.subprocess, "Popen", popen)
    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    return tmp_path, calls, state


# ── start_coordinator ───────────────────────────────────────────────────────────────────────

def test_it_starts_the_coordinator_from_this_interpreter(runtime):
    tmp_path, calls, state = runtime
    state["bus"] = "after-start"

    assert lifecycle.start_coordinator() is True

    argv, kwargs = calls["popen"][0]
    assert argv == [sys.executable, "-c", ENTRY]
    assert "suijin" not in argv, "the launcher on PATH may be a stale installed copy"
    assert kwargs["stdin"] == subprocess.DEVNULL, "a child must never inherit an open stdin"
    assert kwargs["start_new_session"] is True, "it must outlive the console that started it"
    log = tmp_path / ".suijin" / "logs" / "swarm-coordinator.log"
    assert log.is_file(), "the coordinator logs inside the runtime root, not the system temp dir"


def test_it_waits_for_the_bus_before_reporting_ready(runtime):
    _, calls, state = runtime
    assert lifecycle.start_coordinator() is False
    assert len(calls["popen"]) == 1


def test_it_is_not_ready_until_the_schema_exists(runtime):
    _, _, state = runtime
    state["bus"] = ("127.0.0.1", 40000)
    state["schema"] = False

    assert lifecycle.start_coordinator() is False


# ── the hunt's cold start ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def hunt(runtime, monkeypatch):
    _, calls, state = runtime
    seen: dict[str, list] = {"spawn": [], "dropped": [], "started": []}

    def start_coordinator():
        seen["started"].append(True)
        state["bus"] = ("127.0.0.1", 40000)
        return True

    monkeypatch.setattr(lifecycle, "start_coordinator", start_coordinator)
    monkeypatch.setattr(lifecycle, "spawn_capacity_check", lambda host_n, boxed_n: (True, ""))
    monkeypatch.setattr(lifecycle, "run_swarm_spawn", lambda args: seen["spawn"].append(args.swarm) or 0)
    monkeypatch.setattr(lifecycle, "_drop_unclaimed_intent", lambda text, swarm="default": seen["dropped"].append((text, swarm)) or 0)
    return seen


def test_a_cold_hunt_starts_this_trees_coordinator_and_spawns_into_its_own_swarm(runtime, hunt):
    _, calls, _ = runtime

    assert lifecycle.run_swarm_hunt("Map example.test", host_n=1, swarm="demo-session") == 0

    assert hunt["started"] == [True]
    assert hunt["spawn"] == ["demo-session"]
    assert calls["run"] == [], "a hunt must never go through the `suijin` launcher"


def test_a_cold_hunt_leaves_no_intent_in_the_default_swarm(runtime, hunt):
    lifecycle.run_swarm_hunt("Map example.test", host_n=1, swarm="demo-session")

    assert hunt["dropped"] == [("Map example.test", "default")]


def test_a_warm_hunt_does_not_start_another_coordinator(runtime, hunt):
    _, calls, state = runtime
    state["bus"] = ("127.0.0.1", 40000)

    assert lifecycle.run_swarm_hunt("Map example.test", host_n=1, swarm="demo-session") == 0

    assert hunt["started"] == []
    assert calls["popen"] == []


def test_a_hunt_that_cannot_start_a_coordinator_stops_before_spawning(runtime, hunt, monkeypatch):
    monkeypatch.setattr(lifecycle, "start_coordinator", lambda: False)

    assert lifecycle.run_swarm_hunt("Map example.test", host_n=1, swarm="demo-session") == 1
    assert hunt["spawn"] == []


# ── the dashboard's Start engine ───────────────────────────────────────────────────────────


def test_start_engine_reports_started_only_when_the_coordinator_answers(runtime, monkeypatch):
    from suijin.server.web import console_api

    _, _, state = runtime
    monkeypatch.setattr(console_api, "_bus", lambda: None)

    assert console_api.start_daemon({})["started"] is False

    state["bus"] = "after-start"
    assert console_api.start_daemon({})["started"] is True


def test_start_engine_does_not_start_a_second_coordinator_when_one_is_running(runtime, monkeypatch):
    from suijin.server.web import console_api

    _, calls, _ = runtime
    monkeypatch.setattr(console_api, "_bus", lambda: object())

    result = console_api.start_daemon({})

    assert result["started"] is True
    assert calls["popen"] == []
