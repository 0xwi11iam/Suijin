"""Small truths the four code reads of 2026-11-27 found broken. One test each, so they stay fixed."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil

import suijin
from suijin.modules.console.lib import cli
from suijin.server import data as data_layer
from suijin.server.box import hypervisor
from suijin.server.platform.lib import engagement_index
from suijin.server.tools.lib import evidence_store, system_stats
from suijin.server.web import box_status, console_api


def test_the_engagement_index_counts_a_folder_that_has_a_journal_but_no_metadata(tmp_path):
    root = tmp_path / ".suijin"
    (root / "swarm").mkdir(parents=True)
    engagements = root / "engagements"
    (engagements / "a-chat").mkdir(parents=True)
    (engagements / "a-chat" / "engagement.json").write_text(
        json.dumps({"name": "a chat", "kind": "chat", "createdAt": "2026-11-27T00:00:00Z"})
    )
    (engagements / "old-run").mkdir()
    (engagements / "old-run" / "events.jsonl").write_text('{"kind": "tool.call"}\n' * 3)
    (engagements / "empty").mkdir()
    (engagements / ".archive").mkdir()
    rows = {row["id"]: row for row in engagement_index.rebuild(root)["engagements"]}
    assert set(rows) == {"a-chat", "old-run"}  # an empty folder and the archive are not engagements
    assert rows["a-chat"]["kind"] == "chat" and rows["a-chat"]["events"] == 0
    assert rows["old-run"]["kind"] == "engagement" and rows["old-run"]["events"] == 3
    assert rows["old-run"]["createdAt"].startswith("20")  # dated by its journal when nothing else says
    assert (engagements / "index.json").is_file()


def _blob(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_evidence_lands_beside_its_entry_not_one_level_deeper(tmp_path):
    entry = tmp_path / "exploits" / "EXP-001"
    entry.mkdir(parents=True)
    record = evidence_store.capture_into(entry, "proof", "HTTP/1.1 200 OK\n")
    digest = _blob("HTTP/1.1 200 OK\n")
    assert (entry / "evidence" / digest[:2] / f"{digest}.bin").read_text() == "HTTP/1.1 200 OK\n"
    assert (
        json.loads((entry / "evidence" / "evidence.json").read_text())["records"][0]["hash"] == digest == record["hash"]
    )
    assert not (tmp_path / "exploits" / "exploits").exists()


def test_evidence_written_one_level_deeper_is_adopted_and_still_verifies(tmp_path):
    old = tmp_path / "exploits" / "exploits" / "EXP-002" / "evidence"
    old.mkdir(parents=True)
    digest = _blob("older proof\n")
    (old / digest[:2]).mkdir()
    (old / digest[:2] / f"{digest}.bin").write_text("older proof\n")
    record = {"role": "proof", "hash": digest, "algo": "sha256", "len": 12}
    record["self"] = evidence_store._sha(
        json.dumps({k: record[k] for k in ("role", "hash", "len")}, sort_keys=True).encode()
    )
    (old / "evidence.json").write_text(json.dumps({"records": [record]}))
    entry = tmp_path / "exploits" / "EXP-002"
    entry.mkdir()
    evidence_store.capture_into(entry, "proof", "newer proof\n")
    assert not old.parent.parent.exists()  # moved, not copied, and no empty shell left
    assert (entry / "evidence" / digest[:2] / f"{digest}.bin").is_file()
    problems = evidence_store.verify(tmp_path)["entries"]
    assert "EXP-002" not in problems  # the adopted blob still verifies clean


def test_verify_adopts_a_nested_blob_on_its_own(tmp_path):
    nested = tmp_path / "exploits" / "exploits" / "EXP-003" / "evidence" / "ab"
    nested.mkdir(parents=True)
    (nested / "abcd.bin").write_text("x")
    (tmp_path / "exploits" / "EXP-003").mkdir()
    evidence_store.verify(tmp_path)
    assert (tmp_path / "exploits" / "EXP-003" / "evidence" / "ab" / "abcd.bin").is_file()


def test_summon_honours_the_number_that_was_asked_for(monkeypatch):
    asked = []
    monkeypatch.setattr(hypervisor, "engine_available", lambda: True)
    monkeypatch.setattr(
        hypervisor, "pool_refill_to", lambda n, on_log=print, engagement="default": asked.append(n) or n
    )
    assert cli.run_sandbox_cmd("summon", n=7) == 0
    assert cli.run_sandbox_cmd("summon") == 0
    assert asked == [7, 4]  # 4 only when nothing was asked


def test_the_system_block_prints_memory_and_swap_again():
    text = system_stats.render(
        {
            "cpu_percent": 12.0,
            "cpu_per_core": [10, 20],
            "load_avg": [1.0, 1.1, 1.2],
            "mem_used_gb": 8.3,
            "mem_total_gb": 16.0,
            "mem_percent": 52.0,
            "swap_used_gb": 1.1,
            "swap_total_gb": 2.0,
        }
    )
    assert "memory    8.3/16.0 gb (52%)" in text and "swap      1.1/2.0 gb" in text


def test_the_data_layer_reports_the_configured_pile_target_with_the_engine_off(monkeypatch):
    monkeypatch.setattr(hypervisor, "engine_available", lambda: False)
    monkeypatch.setattr(hypervisor, "pool_target", lambda: 3)
    boxes = data_layer.query("boxes")
    assert boxes["available"] is False and boxes["pool_target"] == 3 and boxes["boxes"] == []


def test_prune_reports_how_many_rows_it_removed(tmp_path, monkeypatch):
    import sqlite3
    import time

    db_path = tmp_path / "swarm.db"
    db = sqlite3.connect(str(db_path))
    db.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, status TEXT, last_heartbeat REAL)")
    db.executemany(
        "INSERT INTO agents VALUES (?, ?, ?)",
        [("a", "stopped", time.time()), ("b", "active", time.time()), ("c", "active", 1.0)],
    )
    db.commit()
    db.close()
    monkeypatch.setattr(console_api, "_swarm_db", lambda: db_path)
    assert console_api.prune_stopped() == {"ok": True, "deleted": 2}  # the stopped one and the stale one


def test_the_doctor_states_this_install_s_facts(monkeypatch):
    monkeypatch.setattr(
        console_api, "providers", lambda: {"providers": [{"id": "test", "label": "Test Lab", "configured": True}]}
    )
    monkeypatch.setattr(console_api, "_zai_configured", lambda: True)
    monkeypatch.setattr(console_api, "_cached_diagnostics", lambda key, configured: {})
    monkeypatch.setattr(console_api, "_bus", lambda: None)
    monkeypatch.setattr(console_api, "_runtime", lambda: {"providerId": "test", "model": "lead/model"})
    monkeypatch.setattr(box_status, "snapshot", lambda: {"engine": "not running"})
    report = console_api.doctor()
    assert report["version"] == suijin.__version__ == report["runtime"]["engineVersion"]
    assert report["runtime"]["arch"] == platform.machine()
    assert report["availability"]["apiRuntime"] == {
        "configured": True,
        "valid": True,
        "providerLabel": "Test Lab · lead/model",
    }
    assert report["prerequisites"][1] == {"id": "sandbox-engine", "available": False, "required": False}
    monkeypatch.setattr(box_status, "snapshot", lambda: {"engine": "running"})
    assert console_api.doctor()["prerequisites"][1]["available"] is True


def test_no_background_path_starts_a_container_engine(monkeypatch, tmp_path):
    """Operator, 2026-11-28: "turn off the fucking orbstack too". On macOS the docker CLI starts
    OrbStack whenever it runs — a read-only probe woke it during a code read — so every background
    path (the web console, the data layer, swarm stop) refuses to run docker unless an engine is
    already listening."""
    import subprocess

    from suijin.server.mesh import bus as bus_module
    from suijin.server.swarm import lifecycle

    ran: list = []

    def record(args, *more, **options):
        ran.append([str(a) for a in args])
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(hypervisor, "engine_socket", lambda: "")
    monkeypatch.setattr(subprocess, "run", record)
    monkeypatch.setattr(lifecycle, "_bus_addr", lambda: None)
    monkeypatch.setattr(lifecycle.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(bus_module, "bus_port_file", lambda: tmp_path / "bus.port")

    assert hypervisor.engine_available(wake=False) is False
    assert hypervisor._docker("ps", wake=False) == (1, "no container engine is running")
    assert data_layer.query("boxes")["available"] is False
    assert box_status.refresh_now()["engine"] == "not running"
    assert lifecycle.run_swarm_stop_all() == 0
    assert not [call for call in ran if "docker" in call], ran  # nothing asked docker for anything

    # with an engine already listening, the same stop does remove sandboxes — through the hypervisor
    monkeypatch.setattr(hypervisor, "engine_socket", lambda: "/tmp/docker.sock")
    stopped = []
    monkeypatch.setattr(hypervisor, "sandbox_ls", lambda: [{"name": "suijin-sbx-a-agent-1"}])
    monkeypatch.setattr(hypervisor, "pool_members", lambda: ["suijin-sbx-pool-1"])
    monkeypatch.setattr(hypervisor, "sandbox_stop", lambda name: stopped.append(name) or True)
    lifecycle._stop_sandboxes(None)
    assert stopped == ["suijin-sbx-a-agent-1", "suijin-sbx-pool-1"]


def _listening_socket(*_ignored):
    """A unix socket someone is listening on. macOS caps a socket path at about 104 characters,
    so it lives in a short directory of its own, removed by the caller."""
    import socket
    import tempfile
    import threading

    folder = tempfile.mkdtemp(dir="/tmp", prefix="suijin-sock-")
    path = f"{folder}/live.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    threading.Thread(target=lambda: server.accept(), daemon=True).start()
    return path, server, folder


def test_a_socket_file_is_not_an_engine(tmp_path, monkeypatch):
    """This machine keeps Docker Desktop's socket file while the docker context points at OrbStack.
    Existence said "an engine is listening"; the next docker call woke OrbStack (2026-11-28)."""
    live, server, folder = _listening_socket(tmp_path)
    try:
        assert hypervisor._live_unix_socket(live) is True
    finally:
        server.close()
        shutil.rmtree(folder, ignore_errors=True)
    stale = tmp_path / "stale.sock"
    stale.write_bytes(b"")  # a file where a socket used to be
    assert hypervisor._live_unix_socket(str(stale)) is False
    assert hypervisor._live_unix_socket(str(tmp_path / "absent.sock")) is False


def test_the_sensor_looks_at_the_endpoint_docker_would_use(tmp_path, monkeypatch):
    live, server, folder = _listening_socket(tmp_path)
    try:
        contexts = tmp_path / ".docker" / "contexts" / "meta" / "abc"
        contexts.mkdir(parents=True)
        (contexts / "meta.json").write_text(
            json.dumps({"Name": "current", "Endpoints": {"docker": {"Host": f"unix://{live}"}}})
        )
        (tmp_path / ".docker" / "config.json").write_text(json.dumps({"currentContext": "current"}))
        monkeypatch.setattr(hypervisor.Path, "home", lambda: tmp_path)
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        assert hypervisor.docker_endpoint() == live and hypervisor.engine_socket() == live

        # the context points at a socket that is not listening: no engine, whatever other files exist
        (contexts / "meta.json").write_text(
            json.dumps({"Name": "current", "Endpoints": {"docker": {"Host": "unix://" + str(tmp_path / "gone.sock")}}})
        )
        (tmp_path / "other.sock").write_bytes(b"")
        assert hypervisor.engine_socket() == ""

        # DOCKER_HOST wins over the context
        monkeypatch.setenv("DOCKER_HOST", f"unix://{live}")
        assert hypervisor.engine_socket() == live
    finally:
        server.close()
        shutil.rmtree(folder, ignore_errors=True)


def test_the_provider_list_is_the_registry_with_its_real_state(monkeypatch):
    """Until 2026-11-28 the list was Z.ai plus four hardcoded "unconfigured" names, so the
    Diagnostics page never showed the provider that was actually running."""
    catalog = [
        {"key": "zai", "label": "Z.ai", "configured": True, "defaultModel": ""},
        {"key": "numes", "label": "Numes", "configured": True, "defaultModel": "numes/fast"},
        {"key": "anthropic", "label": "Anthropic", "configured": False, "defaultModel": ""},
    ]
    monkeypatch.setattr(console_api, "provider_catalog", lambda: {"providers": catalog})
    monkeypatch.setattr(console_api, "_runtime", lambda: {"providerId": "numes", "model": "deepseek/x:free"})
    monkeypatch.setattr(
        console_api,
        "_cached_diagnostics",
        lambda pid, configured: (
            {"valid": False, "reason": "untested", "message": "key saved · untested"}
            if configured
            else {"valid": False, "reason": None, "message": "not connected"}
        ),
    )
    result = console_api.providers()
    ids = [provider["id"] for provider in result["providers"]]
    assert ids == ["numes", "z-ai", "anthropic"]  # the active provider first, then configured, then the rest
    numes, zai, anthropic = result["providers"]
    assert numes["active"] is True and numes["configured"] and numes["auth"]["phase"] == "connected"
    assert numes["configuration"]["model"] == "numes/fast"
    assert zai["key"] == "zai" and zai["id"] == "z-ai" and zai["active"] is False  # the id the console has always used
    assert anthropic["configured"] is False and anthropic["auth"]["message"] == "no key saved for Anthropic"
    assert result["active"] == "numes" and result["preference"]["model"] == "deepseek/x:free"
