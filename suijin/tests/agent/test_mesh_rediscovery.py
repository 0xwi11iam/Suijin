"""connect_remote's bad-key rediscovery — the CI flake fix, unit-pinned.

A stale live self-registration (another suite's process, still beat-fresh,
wrong key) can win discovery. The join must: mark the offender, rediscover
PAST it, retry the HELLO once, and land on the real node.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suijin.modules.agent.lib import mesh, mesh_wire  # noqa: E402


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def bind(self, addr):
        pass

    def getsockname(self):
        return ("127.0.0.1", 49998)


class _FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass


class _FakeSrv:
    port = 7


@pytest.fixture()
def rig(monkeypatch):
    calls = {"discover": 0, "hello": 0, "popen": 0}

    def fake_ssh(spec, timeout_s=15.0, **kw):
        calls["discover"] += 1
        # first discovery: the stale impostor (fresher beat); then the real B
        return (
            {"id": "node-111", "pid": 111, "port": 1, "beat": 9.0}
            if calls["discover"] == 1
            else {"id": "node-222", "pid": 222, "port": 2, "beat": 5.0}
        )

    def fake_request(host, port, key, op, **kw):
        calls["hello"] += 1
        if calls["hello"] == 1:  # the first target (through the tunnel) is the impostor
            raise mesh_wire.WireError("bad key")
        return {"ok": True, "me": {"pid": 222, "summary": "the real B", "port": 2}}

    import socket as sk
    import subprocess as sp

    monkeypatch.setattr(sp, "Popen", lambda *a, **k: calls.__setitem__("popen", calls["popen"] + 1) or _FakeProc())
    monkeypatch.setattr(mesh, "_ssh_mesh_port", fake_ssh)
    monkeypatch.setattr(mesh_wire, "request", fake_request)
    monkeypatch.setattr(sk, "socket", lambda *a, **k: _FakeSocket())
    monkeypatch.setattr(sk, "create_connection", lambda *a, **k: _FakeSocket())
    mesh._BAD_HELLO_PIDS.clear()
    mesh._node.update({"me": {"pid": 999, "summary": "A", "phase": "r"}, "remote": {}, "tunnels": []})
    mesh._node["wire_server"] = _FakeSrv()
    monkeypatch.setattr(mesh, "_write_me", lambda me: None)
    yield calls
    mesh._node.update({"me": None, "remote": {}, "tunnels": []})


def test_bad_key_hello_rediscovers_past_the_impostor(rig):
    out = mesh.connect_remote("localhost", "right-key", force=True)
    assert "joined remote node" in out and "the real B" in out, out
    assert rig["discover"] == 2  # rediscovered once
    assert rig["hello"] == 2  # impostor refused, B answered
    assert 111 in mesh._BAD_HELLO_PIDS  # the offender is excluded from now on


def test_discovery_prefers_freshest_then_excludes_offenders(monkeypatch):
    # sorted by beat DESC, self excluded, offenders excluded.
    # (No rig fixture: its fake_ssh would shadow the real discovery path.)
    import os

    found = {
        "nodes": [
            {"id": "self", "pid": os.getpid(), "port": 9, "beat": 10.0},  # us (freshest)
            {"id": "old", "pid": 111, "port": 1, "beat": 9.0},  # offender
            {"id": "real", "pid": 222, "port": 2, "beat": 5.0},
        ]
    }
    import json
    import subprocess as sp2

    mesh._BAD_HELLO_PIDS.add(111)  # learned from an earlier refusal
    monkeypatch.setattr(
        sp2, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": json.dumps(found) + "\n", "stderr": ""})()
    )
    node = mesh._ssh_mesh_port("localhost")
    assert node["id"] == "real"
