"""Mesh v2 PTY — the operator's hands: a real terminal, /connect, the
key typed as the NEXT PLAIN LINE, and proof the key never becomes
guidance (never reaches the model / journal / .sje).

Drives the REAL RunBox stdin loop over a pty against the hermetic sshd
rig — no LLM needed: the mesh join is exactly this surface.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SSHD_PORT = 2223

DRIVER = r"""
import sys, time, os
sys.path.insert(0, {root!r})
from rich.console import Console
from suijin.modules.agent.lib import mesh
from suijin.modules.tools.lib.run_commands import RunBox

mesh.KEYS_DIR_OVERRIDE = __import__('pathlib').Path({keys_dir!r})
mesh.start(summary="pty node A", phase="recon")
box = RunBox().start()
# tee the command output to a file: Rich wraps ANSI on the pty and the
# parent's grep misses the second line of an error
_orig_print = box._out.print
def _tee(*a, **k):
    with open({tee_path!r}, "a") as f:
        f.write(" ".join(str(x) for x in a)[:400] + "\n")
    _orig_print(*a, **k)
box._out.print = _tee
print("PTY-READY", flush=True)
deadline = time.time() + 60
while time.time() < deadline:
    time.sleep(0.2)
    if getattr(box, "_pending_connect", None) is None and mesh._remote_peers():
        print("PTY-JOINED", flush=True)
        break
# the guidance queue after the join — the KEY must not be in it
print("PTY-GUIDANCE:" + repr(box.take_guidance()), flush=True)
time.sleep(2)
"""


def _wait_b_is_freshest(b_pid: int, timeout_s: float = 40.0) -> None:
    """Discovery picks the FRESHEST live registry node. Earlier suites in
    the same pytest process registered THEMSELVES as mesh nodes; their
    wire servers are stopped but the beat entries stay fresh for STALE_S
    — long enough on a slow CI box for this test's discovery to reach a
    dead-key server instead of B. Wait until B is the freshest live node."""
    import time as _t

    from suijin.modules.agent.lib import mesh

    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        freshest_pid = None
        freshest_beat = -1.0
        try:
            entries = list(mesh.mesh_dir().glob("*.json"))
        except OSError:
            return
        for f in entries:
            if f.name.endswith("-state.json") or f.name == "remote-peers.json":
                continue
            try:
                rec = json.loads(f.read_text())
                fp, beat, fport = int(rec.get("pid") or 0), float(rec.get("beat") or 0), int(rec.get("port") or 0)
            except (ValueError, OSError):
                continue
            if not fp or not fport or _t.time() - beat > mesh.STALE_S:
                continue
            try:
                os.kill(fp, 0)
            except (ProcessLookupError, PermissionError):
                continue
            if beat > freshest_beat:
                freshest_beat, freshest_pid = beat, fp
        if freshest_pid in (None, b_pid):
            return
        _t.sleep(2.0)


@pytest.fixture(scope="module")
def sshd(tmp_path_factory):
    if shutil.which("sshd") is None:
        pytest.skip("no sshd binary")
    tmp = tmp_path_factory.mktemp("sshd-pty")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp / "hk")], check=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp / "ck")], check=True)
    ak = tmp / "ak"
    ak.write_text((tmp / "ck.pub").read_text())
    ak.chmod(0o600)
    cfg = tmp / "cfg"
    cfg.write_text(
        # PAM platform-aware: macOS passwordless accounts need it OFF; Ubuntu
        # runners' locked-password runner user needs it ON for pubkey logins
        f"Port {SSHD_PORT}\nListenAddress 127.0.0.1\nHostKey {tmp / 'hk'}\nUsePAM {'no' if sys.platform == 'darwin' else 'yes'}\n"
        "StrictModes no\n"  # CI tmp dirs are world-writable parents
        f"PasswordAuthentication no\nPubkeyAuthentication yes\nAuthorizedKeysFile {ak}\n"
        f"AllowUsers {os.environ.get('USER', 'williamjiang')}\nPidFile none\n"
    )
    try:
        subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10, check=True)
    except Exception:
        pytest.skip("no passwordless sudo for the test sshd")
    # CI runners (and minimal boxes) lack the privilege-separation dir —
    # sshd exits instantly without it, the listener never binds
    subprocess.run(["sudo", "-n", "mkdir", "-p", "/run/sshd"], capture_output=True, timeout=10)
    proc = subprocess.Popen(
        ["sudo", "-n", shutil.which("sshd"), "-D", "-e", "-f", str(cfg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        with socket.socket() as sk:
            if sk.connect_ex(("127.0.0.1", SSHD_PORT)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("pty sshd never came up")
    subprocess.run(["ssh-keygen", "-R", "[localhost]:2223"], capture_output=True, timeout=10)
    # a throwaway agent holding the rig key: the child's ssh offers it via
    # SSH_AUTH_SOCK exactly like the operator's default-key production path
    ag = subprocess.run(["ssh-agent", "-s"], capture_output=True, text=True, timeout=10).stdout
    sock = ag.split("SSH_AUTH_SOCK=")[1].split(";")[0]
    apid = ag.split("echo Agent pid ")[1].split(";")[0]
    env = dict(os.environ, SSH_AUTH_SOCK=sock)
    subprocess.run(["ssh-add", str(tmp / "ck")], env=env, capture_output=True, timeout=10)
    yield {"key": str(tmp / "ck"), "kh": str(tmp / "kh"), "sock": sock}
    subprocess.run(["ssh-add", "-D"], env=env, capture_output=True, timeout=10)
    subprocess.run(["kill", apid], capture_output=True, timeout=5)
    subprocess.run(["sudo", "-n", "kill", str(proc.pid)], capture_output=True, timeout=10)
    proc.wait(timeout=5)


def test_connect_key_typed_in_the_input_box(tmp_path, sshd):
    """Type /connect, then the key as a plain line — the join lands and
    the key is consumed by the command, never queued as guidance."""
    root = str(Path(__file__).resolve().parents[2])
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    (keys_dir / "mesh.key").write_text("pty-integration-key\n")

    # the far side: node B in the sandbox registry
    from suijin.tests.agent.test_mesh_v2_integration import NODE_SCRIPT  # reuse the rig

    b_proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            NODE_SCRIPT.format(root=root, keys_dir=str(keys_dir)).replace("integration-key", "pty-integration-key"),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    b_info = json.loads(b_proc.stdout.readline())
    assert b_info["port"] > 0
    _wait_b_is_freshest(b_info["pid"])

    os.environ["SSH_AUTH_SOCK"] = sshd["sock"]  # the rig key, like a default key
    pid, fd = pty.fork()
    if pid == 0:  # the child: the REAL RunBox + mesh, on a real terminal
        os.environ["TERM"] = "xterm"
        sys.stdout.reconfigure(line_buffering=True)
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-u",
                "-c",
                DRIVER.format(root=root, keys_dir=str(keys_dir), tee_path=str(tmp_path / "tee.txt")),
            ],
        )
        os._exit(1)

    out = b""
    seen_ready = False
    deadline = time.time() + 70

    def _feed(line: str) -> None:
        os.write(fd, (line + "\r").encode())

    typed_key = False
    joined = False
    guidance_line = ""
    try:
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.4)
            if r:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                text = out.decode(errors="replace")
                if not seen_ready and "PTY-READY" in text:
                    seen_ready = True
                    _feed(f"/connect localhost:{SSHD_PORT}")
                if seen_ready and not typed_key and "mesh key for" in text:
                    typed_key = True  # the next plain line is the key — like the operator
                    _feed("pty-integration-key")
                if typed_key and not joined and "PTY-JOINED" in text:
                    joined = True
                if "PTY-GUIDANCE:" in text:
                    guidance_line = text.split("PTY-GUIDANCE:")[1].splitlines()[0]
                    break
        assert seen_ready, "RunBox never came up on the pty"
        assert typed_key, "the key prompt never rendered"
        assert joined, "the join never landed — child transcript tail: " + out.decode(errors="replace")[-1500:].replace(
            "\n", " | "
        )
        # THE RULE: the key was consumed by /connect — it must NOT be guidance
        assert "pty-integration-key" not in guidance_line, guidance_line
        assert guidance_line.strip().startswith("[]"), guidance_line
    finally:
        os.close(fd)
        with suppressing():
            os.waitpid(pid, 0)
        b_proc.terminate()
        b_proc.wait(timeout=5)


class suppressing:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True
