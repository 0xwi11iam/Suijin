"""Mesh v2 — the wire: loopback JSON-lines protocol, key auth, remote
peer bookkeeping, ask budget. The real cross-process + real-tunnel tests
live in test_mesh_v2_integration.py (marked slow)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suijin.modules.agent.lib import mesh, mesh_wire  # noqa: E402


@pytest.fixture()
def srv():
    """A wire server with the v1 mesh handler shape (file writes into a
    temp mesh dir)."""
    mesh.MESH_DIR_OVERRIDE = Path(mesh.mesh_dir())  # keep whatever tests set
    handlers = {op: (lambda req, _o=op: {"echo": _o}) for op in ("PING", "HELLO", "STATE", "STATUS")}
    server = mesh_wire.MeshWireServer("test-key", handlers)
    server.start()
    yield server
    server.stop()


class TestWireProtocol:
    def test_roundtrip_each_op(self, srv):
        for op in ("PING", "HELLO", "STATE", "STATUS"):
            reply = mesh_wire.request("127.0.0.1", srv.port, "test-key", op, me={"pid": 1})
            assert reply["ok"] is True
            assert reply["echo"] == op

    def test_wrong_key_refused(self, srv):
        with pytest.raises(mesh_wire.WireError, match="bad key"):
            mesh_wire.request("127.0.0.1", srv.port, "not-the-key", "PING")

    def test_unknown_op_refused(self, srv):
        with pytest.raises(mesh_wire.WireError, match="unknown op"):
            mesh_wire.request("127.0.0.1", srv.port, "test-key", "EXPLOIT")

    def test_unreachable_peer_is_a_wire_error(self, srv):
        with pytest.raises(mesh_wire.WireError, match="unreachable"):
            mesh_wire.request("127.0.0.1", srv.port + 1, "test-key", "PING", timeout_s=0.5)

    def test_malformed_line_gets_an_error_not_a_crash(self, srv):
        import socket

        # a malformed line cannot be authenticated — the server DROPS the
        # connection rather than engaging (an error reply would invite more)
        with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as c:
            c.sendall(b"this is not json\n")
            data = c.recv(4096)
        assert data == b""
        # and the server still serves the next good client
        assert mesh_wire.request("127.0.0.1", srv.port, "test-key", "PING")["ok"] is True

    def test_repeated_bad_key_drops_the_connection(self, srv):
        import socket

        with (
            socket.create_connection(("127.0.0.1", srv.port), timeout=5) as c,
            c.makefile("wb") as wf,
            c.makefile("rb") as rf,
        ):
            dropped = False
            for _ in range(4):  # past the 3-strike limit
                wf.write(json.dumps({"key": "wrong", "op": "PING"}).encode() + b"\n")
                wf.flush()
                if not rf.readline():
                    dropped = True  # the server closed on us
                    break
            assert dropped, "server should drop a key-guessing peer"


class TestServerHandlers:
    """The mesh module's own handler functions (what each op DOES)."""

    @pytest.fixture(autouse=True)
    def mesh_env(self, tmp_path, monkeypatch):
        mesh.MESH_DIR_OVERRIDE = tmp_path / "mesh"
        mesh.KEYS_DIR_OVERRIDE = tmp_path / "keys"
        mesh._node.update({"me": {"pid": -1, "summary": "t", "phase": "t"}, "remote": {}, "wire_server": None})
        yield
        mesh._node.update({"me": None, "remote": {}})
        mesh.MESH_DIR_OVERRIDE = None  # module state must not leak into later tests
        mesh.KEYS_DIR_OVERRIDE = None

    def _req(self, op, key="root", **f):
        return mesh._handle_wire({"op": op, "key": key, **f})

    def test_hello_registers_remote_peer(self):
        before = len(mesh.peers(refresh_now=True))
        reply = self._req("HELLO", me={"pid": 42, "summary": "far side", "rp": 4242, "peer_id": "far"})
        import os as _os

        assert reply["me"]["pid"] == _os.getpid()
        rec = mesh._remote_peers()["far"]
        assert rec["lp"] == 4242 and rec["remote"] is True
        assert len(mesh.peers(refresh_now=True)) == before + 1

    def test_dm_lands_in_wire_inbox_and_poll_chat_serves_it(self):
        self._req("DM", from_id="far-node", name="remote", text="hello over the wire")
        lines = mesh.poll_chat()
        assert any("RDM" in ln and "hello over the wire" in ln for ln in lines)

    def test_ask_lands_in_asks_file_and_drains_with_reply_directive(self):
        reply = self._req("ASK", from_id="far-node", qid="ab12", question="is the target up?")
        assert reply["qid"] == "ab12"
        drained = mesh.drain_asks()
        assert any("qid=ab12" in ln and "mesh_reply ab12" in ln for ln in drained)

    def test_status_includes_remote_peers(self):
        self._req("HELLO", me={"pid": 42, "summary": "far side", "rp": 4242, "peer_id": "far"})
        text = mesh.status()
        assert "REMOTE far:" in text


class TestAskBudget:
    @pytest.fixture(autouse=True)
    def mesh_env(self, tmp_path):
        mesh.MESH_DIR_OVERRIDE = tmp_path / "mesh"
        mesh.KEYS_DIR_OVERRIDE = tmp_path / "keys"
        mesh._node.update({"me": {"pid": -1}, "remote": {}, "asks_out": {}})
        # one remote peer registered, pointing at a real wire server
        srv = mesh_wire.MeshWireServer("root", {"__any__": lambda req: {}})
        srv.start()
        mesh._register_remote("far", {"lp": srv.port, "summary": "far side"})
        yield srv
        srv.stop()
        mesh._node.update({"me": None, "remote": {}, "asks_out": {}})
        mesh.MESH_DIR_OVERRIDE = None
        mesh.KEYS_DIR_OVERRIDE = None

    def test_one_outstanding_ask_per_peer(self):
        first = mesh.ask_peer("far", "are you seeing the same WAF?")
        assert "queued" in first and "qid" in first
        second = mesh.ask_peer("far", "and now?")
        assert second.startswith("Error") and "already outstanding" in second

    def test_ttl_releases_the_slot(self):
        mesh.ask_peer("far", "stale question")
        # age it past the TTL
        rec = next(iter(mesh._node["asks_out"].values()))
        rec["at"] = time.time() - mesh.ASK_TTL_S - 1
        again = mesh.ask_peer("far", "fresh question after the old one expired")
        assert "queued" in again

    def test_local_peer_ask_is_redirected_to_dm(self):
        assert mesh.ask_peer("no-such-remote", "x").startswith("Error")


class TestHostKeys:
    @pytest.fixture(autouse=True)
    def keys_env(self, tmp_path):
        mesh.KEYS_DIR_OVERRIDE = tmp_path / "keys"
        yield
        mesh.KEYS_DIR_OVERRIDE = None

    def test_first_remember_then_match(self):
        assert mesh.remember_host_key("box.example", "sekrit") == ""
        assert mesh.remember_host_key("box.example", "sekrit") == ""  # same = silent
        assert mesh.host_key("box.example") == "sekrit"

    def test_changed_key_is_an_ssh_style_warning(self):
        mesh.remember_host_key("box.example", "sekrit")
        warn = mesh.remember_host_key("box.example", "other")
        assert "CHANGED" in warn and "Refusing" in warn

    def test_key_file_permissions(self):
        mesh.remember_host_key("box.example", "sekrit")
        mode = mesh._known_hosts().stat().st_mode & 0o777
        assert mode == 0o600
