"""The filesystem session mesh (local v1) + the multi-window concurrency
fix it rode in on.

Use case: the operator opens several terminal windows of hacking AI; the
sessions discover each other, the strip shows | ⚡N sessions, agents
coordinate through an ephemeral groupchat (first context section, last 10
messages) and pairwise DMs, and peers read each other's curated state
digests. All hosts example.com (AGENTS.md).
"""

from __future__ import annotations

import json
import threading

import pytest


@pytest.fixture()
def live_peer():
    """A REAL foreign process (a sleeping child) — the registry now probes
    pid liveness, so fixtures must use live pids, not invented ones."""
    import subprocess
    import sys as _sys

    proc = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
    yield proc.pid
    proc.terminate()
    with __import__("contextlib").suppress(Exception):
        proc.wait(timeout=5)


@pytest.fixture()
def mesh_dir(tmp_path, monkeypatch):
    import suijin.modules.agent.lib.mesh as mesh

    monkeypatch.setattr(mesh, "MESH_DIR_OVERRIDE", tmp_path / "mesh")
    monkeypatch.setattr(mesh, "_node", {"me": None, "threads": []})
    monkeypatch.setattr(mesh, "_peers", {"list": [], "at": 0.0})
    monkeypatch.setattr(mesh, "_tail_state", {"gc": 0.0, "dm": {}, "gc_lines": [], "dm_lines": []})
    (tmp_path / "mesh").mkdir()
    return tmp_path / "mesh"


class TestFileLockConcurrency:
    def test_parallel_writers_lose_nothing(self, tmp_path):
        """THE multi-window bug: unlocked read-modify-write meant last
        writer wins — findings vanished. Parallel writers under the lock
        must all land."""
        from suijin.modules.platform.lib.filelock import atomic_write, locked

        p = tmp_path / "store.json"
        p.write_text(json.dumps({"n": 0}))
        N = 40

        def bump(_):
            for _ in range(20):
                with locked(p):
                    data = json.loads(p.read_text())
                    data["n"] += 1
                    atomic_write(p, json.dumps(data))

        threads = [threading.Thread(target=bump, args=(i,)) for i in range(N)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert json.loads(p.read_text())["n"] == N * 20

    def test_atomic_write_never_tears(self, tmp_path):
        from suijin.modules.platform.lib.filelock import atomic_write

        p = tmp_path / "f.json"
        big = json.dumps({"k": "x" * 50_000})
        stop = threading.Event()
        torn = []

        def reader():
            while not stop.is_set():
                try:
                    json.loads(p.read_text())  # torn write → ValueError
                except FileNotFoundError:
                    continue
                except ValueError:
                    torn.append(True)
                except OSError:
                    continue

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        for _ in range(30):
            atomic_write(p, big)
        stop.set()
        t.join(timeout=2)
        assert not torn

    def test_kg_parallel_adds_all_land(self, tmp_path, monkeypatch):
        import suijin.modules.redteam.lib.intel.knowledge_graph as kg

        monkeypatch.setattr(kg, "GRAPH_PATH", tmp_path / "kg.json")
        errors = []

        def writer(i):
            try:
                for j in range(10):
                    kg.add_constraint("example.com", "behavior", f"r-{i}-{j}", evidence="e")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        cons = kg.get_constraints("example.com")
        total = sum(len(v) for k, v in cons.items() if not str(k).startswith("_"))
        assert total == 80, f"lost updates: {total}/80"


class TestMeshRegistry:
    def test_start_registers_and_peers_exclude_self(self, mesh_dir, live_peer):
        peer_pid = live_peer
        import suijin.modules.agent.lib.mesh as mesh

        mesh.start(summary="hunt example.com", phase="recon")
        me = json.loads((mesh_dir / f"{__import__('os').getpid()}.json").read_text())
        assert me["summary"] == "hunt example.com"
        # a foreign LIVE node appears; self does not
        (mesh_dir / f"{peer_pid}.json").write_text(
            json.dumps({"pid": peer_pid, "summary": "peer", "beat": __import__("time").time()})
        )
        ps = mesh.peers(refresh_now=True)
        assert [p["pid"] for p in ps] == [peer_pid]
        mesh.stop()

    def test_stale_nodes_are_gc(self, mesh_dir):
        import time

        import suijin.modules.agent.lib.mesh as mesh

        (mesh_dir / "1.json").write_text(json.dumps({"pid": 1, "summary": "old", "beat": time.time() - 999}))
        assert mesh.peers(refresh_now=True) == []

    def test_stop_sweeps_chat_when_last(self, mesh_dir):
        import os

        import suijin.modules.agent.lib.mesh as mesh

        mesh.start(summary="solo")
        mesh.broadcast("hello from the only node")
        assert (mesh_dir / "gc.log").exists()
        mesh.stop()
        assert not (mesh_dir / "gc.log").exists()
        assert not (mesh_dir / f"{os.getpid()}.json").exists()


class TestMeshChat:
    def test_broadcast_and_render_last_ten(self, mesh_dir):
        import suijin.modules.agent.lib.mesh as mesh

        mesh.start(summary="a")
        for i in range(14):
            mesh.broadcast(f"message {i}")
        block = mesh.render_chat_block()
        # 14 sent, 10 kept: messages 0-3 dropped, 4-13 present
        assert "message 3" not in block.split("(")[0]
        assert "message 0" not in block and "message 1]" not in block
        assert "message 4" in block and "message 13" in block
        mesh.stop()

    def test_dm_is_pairwise(self, mesh_dir, live_peer):
        import os
        import time

        import suijin.modules.agent.lib.mesh as mesh

        me = os.getpid()
        peer_pid = live_peer
        (mesh_dir / f"{peer_pid}.json").write_text(
            json.dumps({"pid": peer_pid, "summary": "peer", "beat": time.time()})
        )
        mesh.start(summary="a")
        assert "DM sent" in mesh.dm(peer_pid, "pairwise secret")
        # exactly ONE pair file exists — the pair's; no third-party file
        a, b = sorted((me, peer_pid))
        assert (mesh_dir / f"dm-{a}-{b}.log").exists()
        assert len(list(mesh_dir.glob("dm-*.log"))) == 1
        mesh.stop()

    def test_chat_is_ephemeral_by_ruling(self, mesh_dir):
        """Mesh chat must never enter journals/trails/.sje — the runtime
        dir is the only home, and it sweeps with the last node."""

        import suijin.modules.agent.lib.mesh as mesh

        mesh.start(summary="x")
        mesh.broadcast("finding X confirmed")
        mesh.stop()
        assert not (mesh_dir / "gc.log").exists()

    def test_read_peer_digest(self, mesh_dir, live_peer):
        import time

        import suijin.modules.agent.lib.mesh as mesh

        pid = live_peer
        (mesh_dir / f"{pid}.json").write_text(json.dumps({"pid": pid, "summary": "peer", "beat": time.time()}))
        (mesh_dir / f"{pid}-state.json").write_text(
            json.dumps({"pid": pid, "phase": "exploitation", "findings": ["header leak"], "footholds": ["EXP-1: sess"]})
        )
        out = mesh.read_peer_state(str(pid))
        assert "header leak" in out and "EXP-1" in out

    def test_status_lists_nodes(self, mesh_dir, live_peer):
        import time

        import suijin.modules.agent.lib.mesh as mesh

        pid = live_peer
        (mesh_dir / f"{pid}.json").write_text(json.dumps({"pid": pid, "summary": "peer", "beat": time.time()}))
        mesh.start(summary="me")
        out = mesh.status()
        assert f"node-{pid}" in out and "2 session(s) connected" in out
        mesh.stop()


class TestMeshTools:
    def test_dispatch_routes_the_four(self):
        from suijin.modules.tools.lib.dispatch import route_tool

        out = route_tool("mesh_broadcast", {"message": ""}, {})
        assert str(out).startswith("Error")  # empty message refused cleanly
        out = route_tool("mesh_read", {"node": "999999"}, {})
        assert "no such mesh node" in str(out) or "not published" in str(out)


class TestMeshInContext:
    def test_groupchat_rides_first_section(self, mesh_dir):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "_block_parts.insert(0, _mesh_block)" in src, "GC must be the FIRST section"
        assert "MESH_GC" in src, "mesh content must ride untrusted-wrapped"

    def test_strip_segment_present(self):
        import io
        import re

        from rich.console import Console

        import suijin.client.tui.console_ui as cu

        cu.UI_STATE["mesh_count"] = 3
        try:
            ui = cu.EngagementUI(Console(record=True, width=120, force_terminal=True), objective="t")
            sink = Console(file=io.StringIO(), width=120, force_terminal=True)
            sink.print(ui._strip())
            plain = re.sub(r"\x1b\[[0-9;]*m", "", sink.file.getvalue()).replace("\n", " ")
            assert "3 sessions" in plain
        finally:
            cu.UI_STATE["mesh_count"] = 0

    def test_doctrine_mentions_the_mesh(self):
        from suijin.modules.agent.lib.prompts.base import engagement_order

        order = engagement_order("hunt example.com")
        assert "MESH" in order and "mesh_broadcast" in order

    def test_coach_sees_mesh_peers(self):
        from suijin.client.tui.console_ui import UI_STATE
        from suijin.modules.agent.lib import supervisor as sup

        UI_STATE["mesh_count"] = 3
        try:
            brief = sup.facts_brief({}, [])
            assert brief.get("mesh_peers") == 2
        finally:
            UI_STATE["mesh_count"] = 0


class TestGhostNodes:
    def test_dead_pids_do_not_linger(self, mesh_dir):
        """A session killed without stop() (SIGKILL) leaves its registry
        file with a fresh beat — the mtime filter alone showed it as a
        ghost peer for up to 30s. The zero-signal probe settles it now."""
        import time

        import suijin.modules.agent.lib.mesh as mesh

        # pid 99999999 does not exist; beat is fresh
        (mesh_dir / "99999999.json").write_text(json.dumps({"pid": 99999999, "summary": "ghost", "beat": time.time()}))
        assert mesh.peers(refresh_now=True) == []

    def test_live_foreign_pid_is_visible(self, mesh_dir, live_peer):
        import time

        import suijin.modules.agent.lib.mesh as mesh

        pid = live_peer
        (mesh_dir / f"{pid}.json").write_text(json.dumps({"pid": pid, "summary": "live", "beat": time.time()}))
        ps = mesh.peers(refresh_now=True)
        assert any(p["pid"] == pid for p in ps)


class TestMeshEncouragement:
    """Operator ask: 'make sure agent knows how to communicate with the
    other one and it is encouraged to.' Four surfaces: turn-1 doctrine
    (the HOW), compact-order nudge (every turn), the GC header's reply
    how-to, and the coach's unshared-finding fact."""

    def test_full_order_teaches_the_how(self):
        from suijin.modules.agent.lib.prompts.base import engagement_order

        order = engagement_order("hunt example.com")
        assert "mesh_status" in order and "mesh_broadcast" in order and "mesh_dm" in order and "mesh_read" in order
        assert "Communicating is part of the work" in order

    def test_compact_order_keeps_the_nudge(self):
        from suijin.modules.agent.lib.prompts.base import engagement_order

        slim = engagement_order("hunt example.com", compact=True)
        assert "MESH" in slim and "mesh_broadcast" in slim

    def test_gc_header_explains_replying(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "You can reply: mesh_broadcast" in src

    def test_join_notice_fires_once(self):
        import inspect

        from suijin.modules.agent.lib.nodes import think_node

        src = inspect.getsource(think_node)
        assert "_mesh_join_announced" in src, "no one-time join notice"
        assert "you are now a team" in src

    def test_unshared_finding_is_speakworthy(self):
        from suijin.client.tui.console_ui import UI_STATE
        from suijin.modules.agent.lib import supervisor as sup

        UI_STATE["mesh_count"] = 2
        try:
            trace = [
                {"tool_name": "http_request", "success": True},
                {"tool_name": "catalog_exploit", "success": True, "tool_output": "EXP-1 CONFIRMED — HIGH : leak"},
                {"tool_name": "http_request", "success": True},
            ]
            brief = sup.facts_brief({}, trace)
            assert brief.get("unshared_finding") is True
            assert sup._facts_speakworthy(brief) is True
            # shared → not speakworthy on this fact
            trace2 = trace + [{"tool_name": "mesh_broadcast", "success": True}]
            brief2 = sup.facts_brief({}, trace2)
            assert not brief2.get("unshared_finding")
        finally:
            UI_STATE["mesh_count"] = 0
