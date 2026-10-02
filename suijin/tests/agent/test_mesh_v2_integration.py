"""Mesh v2 integration — REAL processes, a REAL sshd, a REAL tunnel.

Topology (hermetic, this machine only):
  * a test sshd on 127.0.0.1:2222 (UsePAM no, tmp host keys, one test
    pubkey authorized — the macOS system sshd refuses passwordless
    accounts via PAM, so the rig brings its own)
  * node B: a subprocess mesh node (the "remote" side, reached ONLY
    through the tunnel)
  * node A: this test process, joining via mesh.connect_remote

Proves: discovery over ssh (mesh-port reads the registry), the twin
(-L/-R) tunnel, HELLO both ways, wire DM, remote STATE reads, the ask
budget, and that killing the tunnel drops the peer.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suijin.modules.agent.lib import mesh  # noqa: E402

SSHD_PORT = 2222
HAS_SUDO = os.environ.get("CI") is None  # local rig: passwordless sudo assumed

NODE_SCRIPT = """
import sys, time, json
sys.path.insert(0, {root!r})
from suijin.modules.agent.lib import mesh
# the REAL mesh dir: the remote-side `suijin mesh-port` (a separate
# process, no overrides) discovers nodes through the registry — exactly
# the production path. Keys stay tmp: never the operator's own.
mesh.KEYS_DIR_OVERRIDE = __import__('pathlib').Path({keys_dir!r})
mesh.start(summary="node B far side", phase="recon")
mesh.publish_state({{"findings": ["B-seen-ssrf-attempt"], "phase": "recon"}})
print(json.dumps({{"pid": mesh._node['me']['pid'], "port": mesh._node['wire_server'].port}}), flush=True)
while True:
    time.sleep(0.5)
"""


def _have(key, val, path):
    out = subprocess.run(key, capture_output=True, text=True, timeout=30)
    assert val in out.stdout, f"{path} missing: {out.stderr[:200]}"
    return out.stdout.strip()


@pytest.fixture(scope="module")
def sshd(tmp_path_factory):
    """A real sshd on 127.0.0.1:2222 — no PAM, one authorized test key."""
    if shutil.which("sshd") is None:
        pytest.skip("no sshd binary on this machine")
    tmp = tmp_path_factory.mktemp("sshd")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp / "host_key")], check=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp / "client_key")], check=True)
    ak = tmp / "authorized_keys"
    ak.write_text((tmp / "client_key.pub").read_text())
    ak.chmod(0o600)
    cfg = tmp / "sshd_config"
    cfg.write_text(
        f"Port {SSHD_PORT}\n"
        "ListenAddress 127.0.0.1\n"
        f"HostKey {tmp / 'host_key'}\n"
        # PAM: macOS must disable it (passwordless accounts refuse otherwise);
        # UBUNTU RUNNERS need it ON — the runner user's password is locked
        # and a no-PAM sshd refuses pubkey logins for locked accounts.
        f"UsePAM {'no' if sys.platform == 'darwin' else 'yes'}\n"
        "StrictModes no\n"  # CI tmp dirs are world-writable parents — sshd rejects the key otherwise
        "PasswordAuthentication no\n"
        "PubkeyAuthentication yes\n"
        f"AuthorizedKeysFile {ak}\n"
        f"AllowUsers {os.environ.get('USER', 'williamjiang')}\n"
        "PidFile none\n"
    )
    # sshd needs root for privilege separation on macOS; the local rig has
    # passwordless sudo (CI: skip instead of failing)
    sshd_bin = shutil.which("sshd") or "/usr/sbin/sshd"
    try:
        subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10, check=True)
    except Exception:
        pytest.skip("no passwordless sudo for the test sshd")
    # CI runners (and minimal boxes) lack the privilege-separation dir —
    # sshd exits instantly without it, the listener never binds
    subprocess.run(["sudo", "-n", "mkdir", "-p", "/run/sshd"], capture_output=True, timeout=10)
    proc = subprocess.Popen(
        ["sudo", "-n", sshd_bin, "-D", "-e", "-f", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # wait for the listener
    for _ in range(50):
        with socket.socket() as sk:
            if sk.connect_ex(("127.0.0.1", SSHD_PORT)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.terminate()
        err = proc.communicate(timeout=5)[1].decode(errors="replace")
        pytest.fail(f"test sshd never came up: {err[:300]}")
    subprocess.run(["ssh-keygen", "-R", "[localhost]:2222"], capture_output=True, timeout=10)
    yield {"port": SSHD_PORT, "client_key": str(tmp / "client_key"), "kh": str(tmp / "known_hosts")}
    subprocess.run(["sudo", "-n", "kill", str(proc.pid)], capture_output=True, timeout=10)
    proc.wait(timeout=5)


@pytest.fixture()
def node_b(tmp_path, sshd):
    """The 'remote' mesh node — a subprocess reachable ONLY via :2222."""
    root = str(Path(__file__).resolve().parents[2])
    mesh_dir = tmp_path / "mesh"
    keys_dir = tmp_path / "keys"
    mesh_dir.mkdir()
    keys_dir.mkdir()
    (keys_dir / "mesh.key").write_text("integration-key\n")
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", NODE_SCRIPT.format(root=root, keys_dir=str(keys_dir))],
        stdout=subprocess.PIPE,
        text=True,
    )
    info = json.loads(proc.stdout.readline())
    mesh.MESH_DIR_OVERRIDE = None  # any leaked unit-test override lies about
    # which registry B wrote (field interference, 2026-10-02)
    yield {"proc": proc, **info, "mesh_dir": mesh.mesh_dir(), "keys_dir": keys_dir}
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture()
def node_a(node_b):
    """THIS process as the joining node (own runtime dir — the far side is
    only reachable through the tunnel)."""
    mesh.MESH_DIR_OVERRIDE = None  # the suite sandbox — B is in it too
    mesh.KEYS_DIR_OVERRIDE = node_b["mesh_dir"].parent / "keys-a"
    mesh.KEYS_DIR_OVERRIDE.mkdir(parents=True, exist_ok=True)
    (mesh.KEYS_DIR_OVERRIDE / "mesh.key").write_text("integration-key\n")  # same key both sides
    mesh._node.update({"me": None, "remote": {}, "tunnels": [], "asks_out": {}})
    if mesh._node.get("wire_server"):
        mesh._node["wire_server"].stop()
    mesh.start(summary="node A local side", phase="recon")
    yield node_b
    for t in mesh._node.pop("tunnels", []):
        t.terminate()
    mesh.stop()


def _join(sshd):
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return mesh.connect_remote(
        f"localhost:{sshd['port']}",
        "integration-key",
        identity=sshd["client_key"],
        kh_file=sshd["kh"],
        remote_workspace=str(WORKSPACE_DIR),
    )


def test_full_remote_join_cycle(node_a, sshd):
    out = _join(sshd)
    assert "joined remote node" in out, out
    assert "node B far side" in out

    # both sides see each other
    roster = mesh.status()
    assert "REMOTE localhost:" in roster and "node B far side" in roster

    # A DMs B over the wire — B's inbox gets the line (read through the
    # shared tmpfs, on real machines it arrives over the wire only)
    r = mesh.dm_remote("localhost", "wire hello from A")
    assert not str(r).startswith("Error"), r
    deadline = time.time() + 5
    inbox = node_a["mesh_dir"] / "wire-inbox.log"
    while time.time() < deadline:
        if inbox.exists() and "wire hello from A" in inbox.read_text():
            break
        time.sleep(0.2)
    assert "wire hello from A" in inbox.read_text()

    # A reads B's published state (remote STATE op through the tunnel)
    state = mesh.read_peer_state("localhost")
    assert "B-seen-ssrf-attempt" in state

    # the ask budget: one outstanding per peer
    assert "queued" in mesh.ask_peer("localhost", "is the WAF logging you?")
    assert "already outstanding" in mesh.ask_peer("localhost", "again?")

    # ── tunnel death drops the peer ─────────────────────────────
    for proc in mesh._node.pop("tunnels", []):
        proc.terminate()
    deadline = time.time() + 25  # 3 missed polls × 5s + slack
    while time.time() < deadline:
        if not any(p.get("remote") for p in mesh.peers(refresh_now=True)):
            break
        time.sleep(1.0)
    assert not any(p.get("remote") for p in mesh.peers(refresh_now=True)), "dead tunnel must drop the peer"


def test_wrong_key_refused_at_hello(node_a, sshd):
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    out = mesh.connect_remote(
        f"localhost:{sshd['port']}",
        "WRONG-key",
        identity=sshd["client_key"],
        kh_file=sshd["kh"],
        remote_workspace=str(WORKSPACE_DIR),
        force=True,  # past the fingerprint check: the HELLO auth is under test
    )
    assert out.startswith("Error"), out
    assert "bad key" in out or "refused" in out
