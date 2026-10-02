"""The filesystem session mesh (local v1) — terminal windows that connect
and talk.

Operator's use case: open N terminal windows of hacking AI; they discover
each other, show '| ⚡N sessions' in the strip, and the agents coordinate
through a group chat (first context section, last 10 messages) and
pairwise DMs. Transport is FILES in a runtime dir — same machine, same
user, no servers, no ports, no auth (remote nodes are v2 and get real
transport).

EPHEMERAL BY RULING: mesh chat never enters journals, audit trails, or
.sje bundles. The runtime dir sweeps when the last node exits.

INJECTION DEFENSE: everything mesh-originated is served to the model
inside the UNTRUSTED wrappers (same boundary as tool output) — a peer's
message is data from another agent, never an instruction.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

GC_KEEP = 10  # messages rendered into context
POLL_S = 5.0
BEAT_S = 2.0
STALE_S = 30.0

_node: dict = {"me": None, "threads": []}
#: overridable for tests (module attr, checked at call time)
MESH_DIR_OVERRIDE: Path | None = None


def mesh_dir() -> Path:
    """Runtime dir (honours a monkeypatched module attr for tests)."""
    v = globals().get("MESH_DIR_OVERRIDE")
    if v is not None:
        return Path(v)
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR.parent / "mesh"


def _me_file() -> Path:
    return mesh_dir() / f"{os.getpid()}.json"


def _gc_log() -> Path:
    return mesh_dir() / "gc.log"


def _dm_file(other_pid: int) -> Path:
    a, b = sorted((os.getpid(), other_pid))
    return mesh_dir() / f"dm-{a}-{b}.log"


# ── node lifecycle ───────────────────────────────────────────────────────


def start(summary: str = "", phase: str = "starting") -> None:
    """Register this session as a mesh node; start heartbeat + poll +
    the loopback wire server (v2).

    Idempotent — a process is exactly one node."""
    if _node["me"] is not None:
        return
    mesh_dir().mkdir(parents=True, exist_ok=True)
    me = {"pid": os.getpid(), "summary": summary[:120], "phase": phase, "started": time.time()}
    _node["me"] = me
    _write_me(me)
    with contextlib.suppress(Exception):
        _start_wire()
        me["port"] = _node["wire_server"].port
        _write_me(me)
    stop_evt = threading.Event()

    def _beat():
        while not stop_evt.wait(BEAT_S):
            with contextlib.suppress(OSError):
                _write_me(_node["me"] or me)

    def _poll():
        while not stop_evt.wait(POLL_S):
            with contextlib.suppress(Exception):
                refresh()

    for target in (_beat, _poll):
        t = threading.Thread(target=target, daemon=True, name=f"mesh-{target.__name__}")
        t.start()
        _node["threads"].append(t)
    _node["stop"] = stop_evt
    import atexit

    atexit.register(stop)


def stop() -> None:
    """Unregister; sweep the runtime dir when the last node leaves; take
    the wire server and any tunnels down with us."""
    with contextlib.suppress(Exception):
        (_node.get("stop") or threading.Event()).set()
        for proc in _node.pop("tunnels", []) or []:
            proc.terminate()
        srv = _node.pop("wire_server", None)
        if srv is not None:
            srv.stop()
        _me_file().unlink(missing_ok=True)
        (mesh_dir() / "remote-peers.json").unlink(missing_ok=True)
        if not list(mesh_dir().glob("*.json")):
            # last one out: chat is ephemeral by ruling — sweep it
            for p in (
                mesh_dir() / "gc.log",
                mesh_dir() / "wire-inbox.log",
                mesh_dir() / "wire-asks.jsonl",
                *mesh_dir().glob("dm-*.log"),
            ):
                p.unlink(missing_ok=True)
    _node["me"] = None


def _write_me(me: dict) -> None:
    from suijin.modules.platform.lib.filelock import atomic_write

    me["beat"] = time.time()
    atomic_write(_me_file(), json.dumps(me))


def set_phase(phase: str) -> None:
    if _node["me"] is not None:
        _node["me"]["phase"] = str(phase)[:40]
        _write_me(_node["me"])


def publish_state(digest: dict) -> None:
    """Refresh the per-turn mesh-state digest peers may read (curated:
    board/findings/footholds/phase — NEVER operator guidance or DMs)."""
    if _node["me"] is None:
        return
    from suijin.modules.platform.lib.filelock import atomic_write

    with contextlib.suppress(OSError):
        atomic_write(
            mesh_dir() / f"{os.getpid()}-state.json",
            json.dumps({"pid": os.getpid(), **digest}, default=str),
        )


# ── discovery ────────────────────────────────────────────────────────────

_peers: dict = {"list": [], "at": 0.0}


def peers(refresh_now: bool = False) -> list[dict]:
    """Live peer nodes (self excluded). Cached between polls; a fresh
    read when asked for or when the cache is older than POLL_S.
    v2: remote (tunneled) peers ride the same list, tagged remote=True."""
    now = time.time()
    if refresh_now or now - _peers["at"] >= POLL_S:
        live = []
        with contextlib.suppress(OSError):
            for f in mesh_dir().glob("*.json"):
                if f.name.endswith("-state.json") or f.name == "remote-peers.json":
                    continue
                with contextlib.suppress(Exception):
                    rec = json.loads(f.read_text())
                    pid = int(rec.get("pid") or 0)
                    if pid and pid != os.getpid() and now - float(rec.get("beat") or 0) < STALE_S:
                        # PROCESS LIVENESS: a SIGKILLed session leaves its
                        # registry file behind; the beat filter alone showed
                        # it as a ghost for up to STALE_S seconds. One
                        # zero-signal probe settles it instantly.
                        try:
                            os.kill(pid, 0)
                        except (ProcessLookupError, PermissionError):
                            continue
                        live.append(rec)
        live += [dict(r) for r in _remote_peers().values() if int(r.get("missed") or 0) < 3]
        _peers["list"] = live
        _peers["at"] = now
    return _peers["list"]


def refresh() -> None:
    peers(refresh_now=True)
    with contextlib.suppress(Exception):
        _ping_remotes()
    from suijin.client.tui.console_ui import UI_STATE

    UI_STATE["mesh_count"] = len(_peers["list"]) + (1 if _node["me"] else 0)


def resolve(ref: str) -> int | None:
    """'1234', 'node-1234', or a summary prefix → pid."""
    ref = str(ref or "").strip().lower().lstrip("node-")
    for p in peers(refresh_now=True):
        if str(p.get("pid")) == ref or ref in str(p.get("summary", "")).lower():
            return int(p.get("pid"))
    return None


# ── chat ─────────────────────────────────────────────────────────────────


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def broadcast(message: str, name: str = "") -> str:
    """Append to the group chat (all nodes see it; ephemeral)."""
    msg = str(message or "").strip().replace("\n", " ")[:400]
    if not msg:
        return "Error: empty message"
    with contextlib.suppress(OSError):
        mesh_dir().mkdir(parents=True, exist_ok=True)
        with _gc_log().open("a", encoding="utf-8") as f:
            f.write(f"{_stamp()}|{os.getpid()}|{name or 'agent'}|{msg}\n")
        _tail_reset()
        return f"broadcast to mesh ({len(peers())} peers)"
    return "Error: mesh unavailable"


def dm(other_pid: int, message: str, name: str = "") -> str:
    """Pairwise DM — only the pair's file; no other node reads it."""
    msg = str(message or "").strip().replace("\n", " ")[:400]
    if not msg:
        return "Error: empty message"
    with contextlib.suppress(OSError):
        mesh_dir().mkdir(parents=True, exist_ok=True)
        with _dm_file(int(other_pid)).open("a", encoding="utf-8") as f:
            f.write(f"{_stamp()}|{os.getpid()}|{name or 'agent'}|{msg}\n")
        return f"DM sent to node-{other_pid}"
    return "Error: mesh unavailable"


def dm_remote(ref: str, message: str, name: str = "") -> str:
    """DM a REMOTE peer over the wire (v2). Falls back to the error when
    the ref names no remote peer."""
    msg = str(message or "").strip().replace("\n", " ")[:400]
    if not msg:
        return "Error: empty message"
    out = _wire_route("DM", ref, text=msg, name=name or "agent")
    if out is None:
        return f"Error: no such REMOTE peer: {ref}"
    return f"DM sent to remote {ref}" if not str(out).startswith("Error") else str(out)


def _tail(path: Path, offset: float) -> tuple[list[str], float]:
    """Lines appended to `path` since `offset` (mtime gate)."""
    with contextlib.suppress(OSError):
        mtime = path.stat().st_mtime
        if mtime <= offset:
            return [], offset
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        return lines, mtime
    return [], offset


_tail_state: dict = {"gc": 0.0, "dm": {}, "gc_lines": [], "dm_lines": [], "wire": 0.0}


def _tail_reset() -> None:
    _tail_state["gc"] = 0.0
    _tail_state["dm"] = {}
    _tail_state["wire"] = 0.0


def poll_chat() -> list[str]:
    """New GC + DM-to-me lines since the last poll, oldest first.

    Called once per think turn; the returned lines ride the context
    (untrusted-wrapped by the caller). v2: wire-delivered lines (remote
    DMs, asks, replies) tail the same way."""
    out: list[str] = []
    gc_lines, _tail_state["gc"] = _tail(_gc_log(), _tail_state["gc"])
    out += [f"GC {ln}" for ln in gc_lines]
    dm_out = []
    for p in peers():
        pid = int(p.get("pid") or 0)
        if not pid or p.get("remote"):
            continue
        lines, off = _tail(_dm_file(pid), _tail_state["dm"].get(pid, 0.0))
        _tail_state["dm"][pid] = off
        # only lines addressed TO me (I tail pair files; my own writes are
        # excluded so a DM isn't echoed back to its sender)
        dm_out += [
            f"DM {ln}" for ln in lines if ln.split("|")[1:2] == [str(os.getpid())] or ln.split("|")[1:2] != [str(pid)]
        ]
    wire_lines, _tail_state["wire"] = _tail(_wire_inbox(), _tail_state.get("wire", 0.0))
    out += [f"RDM {ln}" for ln in wire_lines]
    return out + dm_out


def render_chat_block() -> str:
    """The GROUPCHAT context section: last GC_KEEP messages + new DMs.

    Content served RAW here — the caller wraps it untrusted before it
    reaches the model."""
    gc_lines, _ = _tail(_gc_log(), 0.0)
    if not gc_lines and not _tail_state["dm_lines"]:
        return ""
    rows = []
    for ln in gc_lines[-GC_KEEP:]:
        with contextlib.suppress(Exception):
            _ts, pid, name, msg = ln.split("|", 3)
            rows.append(f"[node-{pid} {name} {_ts}] {msg}")
    for ln in _tail_state["dm_lines"][-5:]:
        with contextlib.suppress(Exception):
            _ts, pid, name, msg = ln.split("|", 3)
            rows.append(f"[DM node-{pid} {name} {_ts}] {msg}")
    if not rows:
        return ""
    return "\n".join(rows)


def collect_dm_lines() -> None:
    """Fold new DM-to-me lines into the render cache (called by poll)."""
    _tail_state["dm_lines"] = [ln for ln in _tail_state.get("dm_lines", [])][-5:]
    for p in peers():
        pid = int(p.get("pid") or 0)
        if not pid:
            continue
        lines, off = _tail(_dm_file(pid), _tail_state["dm"].get(pid, 0.0))
        _tail_state["dm"][pid] = off
        for ln in lines:
            parts = ln.split("|")
            if len(parts) >= 4 and parts[1] != str(os.getpid()):
                _tail_state["dm_lines"].append(ln)
    _tail_state["dm_lines"] = _tail_state["dm_lines"][-5:]


def read_peer_state(ref: str) -> str:
    """mesh_read: the peer's curated digest (board/findings/footholds/
    phase). Never operator guidance, never DMs, never raw prompts."""
    remote = _wire_route("STATE", ref)
    if remote is not None:
        return remote
    pid = resolve(ref)
    if not pid:
        return f"Error: no such mesh node: {ref}"
    p = mesh_dir() / f"{pid}-state.json"
    if not p.exists():
        return f"node-{pid} has not published state yet"
    with contextlib.suppress(Exception):
        d = json.loads(p.read_text())
        return json.dumps(d, indent=1, default=str)[:4000]
    return f"Error: unreadable state for node-{pid}"


def status() -> str:
    """mesh_status: the live mesh roster."""
    me = _node["me"] or {}
    rows = [f"self node-{os.getpid()}: {me.get('summary', '')} (phase {me.get('phase', '?')})"]
    ps = peers(refresh_now=True)
    for p in ps:
        if p.get("remote"):
            rows.append(f"REMOTE {p.get('peer_id')}: {str(p.get('summary', ''))[:80]} (phase {p.get('phase', '?')})")
        else:
            rows.append(f"node-{p.get('pid')}: {str(p.get('summary', ''))[:80]} (phase {p.get('phase', '?')})")
    rows.append(f"{len(ps) + 1} session(s) connected")
    return "\n".join(rows)


# ══════════════════════════════════════════════════════════════════════════
# v2 — the wire: remote peers over an SSH tunnel
#
# Posture (v1 ruling extended): LOOPBACK PLAINTEXT ONLY. Each node hosts
# a JSON-lines server on 127.0.0.1 (mesh_wire); a remote peer is joined
# by `/connect user@host`, which runs ONE ssh carrying BOTH forwards:
#   -L 127.0.0.1:<lp>:127.0.0.1:<their_port>   (us → their server)
#   -R 127.0.0.1:<rp>:127.0.0.1:<our_port>     (them → our server)
# The tunnel is the TLS; the node key is the auth. Everything the wire
# delivers lands in the same runtime files the filesystem mesh reads,
# so context rendering, ephemerality, and the untrusted boundary are all
# inherited from v1 unchanged.
# ══════════════════════════════════════════════════════════════════════════

_KEYS_DIR = Path.home() / ".suijin" / "keys"
#: overridable for tests
KEYS_DIR_OVERRIDE: Path | None = None
ASK_TTL_S = 600.0  # an unanswered cross-node ask stops blocking after 10 min


def keys_dir() -> Path:
    v = globals().get("KEYS_DIR_OVERRIDE")
    return Path(v) if v is not None else _KEYS_DIR


def mesh_key() -> str:
    """THIS node's key (default 'root' until the operator changes it —
    the stashed plan's default; a remote join with a different key fails
    auth exactly like ssh)."""
    kd = keys_dir()
    p = kd / "mesh.key"
    try:
        kd.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("root\n", encoding="utf-8")
            p.chmod(0o600)
        return p.read_text(encoding="utf-8").strip() or "root"
    except OSError:
        return "root"


def _known_hosts() -> Path:
    return keys_dir() / "known_hosts.json"


def _fp(key: str) -> str:
    import hashlib

    return hashlib.sha256(str(key).encode()).hexdigest()[:16]


def remember_host_key(host: str, key: str) -> str:
    """Store (or verify) a remote's key. Returns '' when fine, or the
    SSH-style warning when the fingerprint CHANGED (host identity swap —
    the operator must remove the entry or force)."""
    hosts = {}
    with contextlib.suppress(Exception):
        hosts = json.loads(_known_hosts().read_text(encoding="utf-8"))
    entry = hosts.get(host) or {}
    fp = _fp(key)
    if entry.get("fp") and entry["fp"] != fp:
        return (
            f"WARNING: mesh key for {host} CHANGED (was {entry['fp']}, now {fp}) — "
            "possible host swap. Refusing. Edit known_hosts.json to trust the new key."
        )
    if not entry.get("fp"):
        hosts[host] = {"fp": fp, "key": str(key), "added_at": time.strftime("%Y-%m-%d")}
        _known_hosts().parent.mkdir(parents=True, exist_ok=True)
        _known_hosts().write_text(json.dumps(hosts, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            _known_hosts().chmod(0o600)
    return ""


def host_key(host: str) -> str:
    with contextlib.suppress(Exception):
        hosts = json.loads(_known_hosts().read_text(encoding="utf-8"))
        return str(hosts.get(host, {}).get("key") or "")
    return ""


# ── remote peer bookkeeping ──────────────────────────────────────────────


def _remote_peers() -> dict:
    return _node.setdefault("remote", {})


def _wire_inbox() -> Path:
    return mesh_dir() / "wire-inbox.log"


def _wire_asks() -> Path:
    return mesh_dir() / "wire-asks.jsonl"


def _me_wire_entry() -> dict:
    me = dict(_node["me"] or {})
    me["pid"] = os.getpid()
    srv = _node.get("wire_server")
    if srv is not None:
        me["port"] = srv.port
    return me


def _register_remote(peer_id: str, record: dict) -> None:
    record = {**record, "peer_id": peer_id, "remote": True, "missed": 0, "at": time.time()}
    _remote_peers()[peer_id] = record
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.filelock import atomic_write

        atomic_write(
            mesh_dir() / "remote-peers.json",
            json.dumps(list(_remote_peers().values()), default=str),
        )


def _drop_remote(peer_id: str) -> None:
    _remote_peers().pop(peer_id, None)


def _ping_remotes() -> None:
    """One wire PING per remote peer per poll; 3 consecutive misses drops
    the peer (tunnel died — /connect again to rejoin)."""
    from suijin.modules.agent.lib import mesh_wire

    for peer_id, rec in list(_remote_peers().items()):
        try:
            mesh_wire.request("127.0.0.1", int(rec.get("lp") or 0), mesh_key(), "PING", timeout_s=3.0)
            rec["missed"] = 0
        except Exception:  # noqa: BLE001 — a dead tunnel is data, not a crash
            rec["missed"] = int(rec.get("missed") or 0) + 1
            if rec["missed"] >= 3:
                _drop_remote(peer_id)


# ── the join: /connect user@host ────────────────────────────────────────


def _shlex_quote(s: str) -> str:
    import shlex as _sh

    return _sh.quote(s)


def _ssh_mesh_port(
    spec: str,
    timeout_s: float = 15.0,
    *,
    port: int | None = None,
    identity: str | None = None,
    kh_file: str | None = None,
    remote_workspace: str | None = None,
) -> dict:
    """Ask the remote which port its mesh server bound. `suijin mesh-port`
    runs THERE and prints one JSON line — the only remote-side dependency
    (both ends run suijin by definition of this product)."""
    import subprocess

    # a login shell so PATH carries ~/.local/bin (where install.sh puts
    # the launcher); the fallback covers non-login setups.
    # remote_workspace (rare: hermetic tests, multi-install machines)
    # points the far-side launcher at the registry we actually wrote.
    # three fallbacks, each with the workspace prefix, stopping at the
    # first one that prints JSON: PATH launcher, the installer's
    # location, and the repo checkout with its python (CI runners have
    # no launcher installed at all)
    import sys as _sys

    repo = str(Path(__file__).resolve().parents[3])
    py = _sys.executable
    ws = remote_workspace or "$HOME/.suijin/workspace"
    inner = (
        f"export SUIJIN_WORKSPACE={ws}; "
        f"suijin mesh-port 2>/dev/null && exit 0; "
        f"~/.local/bin/suijin mesh-port 2>/dev/null && exit 0; "
        f"cd {repo} && {py} -c 'from suijin.modules.console.lib.cli import run_mesh_port; run_mesh_port()'"
    )
    remote_cmd = "sh -lc " + _shlex_quote(inner)
    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "BatchMode=yes",  # never prompt: a TUI is not always there to answer
        "-o",
        "StrictHostKeyChecking=accept-new",  # first-connect trust, ssh-style
    ]
    if port:
        cmd += ["-p", str(port)]
    if identity:
        cmd += ["-i", str(identity)]
    if kh_file:
        cmd += ["-o", f"UserKnownHostsFile={kh_file}"]
    cmd += [spec, remote_cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        if r.returncode == 0 and r.stdout.strip():
            found = json.loads([ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")][-1])
            # the roster may include the JOINER itself (same-machine sandboxes,
            # shared registry): pick the freshest node that is not us
            nodes = found.get("nodes") or ([found] if found.get("port") else [])
            mine = os.getpid()
            for n in nodes:
                if int(n.get("pid") or 0) != mine:
                    return n
            raise RuntimeError(f"the only mesh node at {spec} is this process")
        raise RuntimeError(
            f"cannot reach a suijin mesh node at {spec} (is a session running there?) "
            f"[ssh exit {r.returncode}: stderr={(r.stderr or '')[:150]} stdout={(r.stdout or '')[:150]}]"
        )
    except (subprocess.TimeoutExpired, ValueError, OSError, IndexError) as e:
        raise RuntimeError(f"cannot reach a suijin mesh node at {spec} ({type(e).__name__}: {e})") from e


def connect_remote(
    spec: str,
    key: str,
    *,
    force: bool = False,
    port: int | None = None,
    identity: str | None = None,
    kh_file: str | None = None,
    remote_workspace: str | None = None,
) -> str:
    """Join a remote node: remember/verify the key, discover its port,
    raise the twin tunnel, HELLO both ways. Human-readable result.
    `spec` is user@host[:port]; port/identity/kh_file are plumbing for
    hermetic tests and exotic setups."""
    spec = str(spec or "").strip()
    if ":" in spec:
        spec, _, port_s = spec.rpartition(":")
        if port_s.isdigit():
            port = int(port_s)
    if not spec or "@" not in spec and "." not in spec and spec != "localhost":
        return "Error: usage /connect user@host"
    warn = remember_host_key(spec, key)
    if warn and not force:
        return f"Error: {warn}"

    theirs = _ssh_mesh_port(spec, port=port, identity=identity, kh_file=kh_file, remote_workspace=remote_workspace)
    srv = _node.get("wire_server")
    if srv is None:
        return "Error: this session's mesh server is not running"

    # twin forwards: lp → their server, rp → back to ours
    import socket as _s

    def _free_port() -> int:
        with _s.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            return sk.getsockname()[1]

    lp, rp = _free_port(), _free_port()
    import subprocess

    cmd = [
        "ssh",
        "-N",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",  # never prompt: a TUI is not always there to answer
    ]
    if port:
        cmd += ["-p", str(port)]
    if identity:
        cmd += ["-i", str(identity)]
    if kh_file:
        cmd += ["-o", f"UserKnownHostsFile={kh_file}"]
    cmd += [
        "-L",
        f"127.0.0.1:{lp}:127.0.0.1:{int(theirs['port'])}",
        "-R",
        f"127.0.0.1:{rp}:127.0.0.1:{srv.port}",
        spec,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _node.setdefault("tunnels", []).append(proc)

    # wait for the forward to accept, then HELLO both directions
    from suijin.modules.agent.lib import mesh_wire

    for _ in range(40):  # ≤8s for ssh to establish
        if proc.poll() is not None:
            return f"Error: ssh tunnel died immediately (exit {proc.returncode}) — check `ssh {spec}` by hand"
        with contextlib.suppress(OSError), _s.create_connection(("127.0.0.1", lp), timeout=0.5):
            break
        time.sleep(0.2)
    else:
        return "Error: tunnel forward never came up (8s)"

    mine = _me_wire_entry()
    mine["rp"] = rp  # how THEY dial back to us
    try:
        reply = mesh_wire.request("127.0.0.1", lp, key, "HELLO", me=mine, timeout_s=8.0)
    except Exception as e:  # noqa: BLE001
        return f"Error: HELLO refused ({e}) — key mismatch? The remote's mesh key must match yours"
    their_me = reply.get("me") or {}
    _register_remote(spec, {**their_me, "lp": lp})
    return (
        f"joined remote node {spec} (their node-{their_me.get('pid', '?')}: "
        f"{str(their_me.get('summary', ''))[:60]}) — mesh is now cross-machine"
    )


def _handle_wire(req: dict) -> dict:
    """The server side: what each op DOES (the wire handled auth)."""
    op = str(req.get("op") or "").upper()

    if op == "PING":
        return {}
    if op == "HELLO":
        theirs = dict(req.get("me") or {})
        peer_id = str(theirs.get("peer_id") or "unknown-peer")
        # THEY connected to US through their -R port; that loopback port is
        # how we dial back (their tunnel, our perspective)
        _register_remote(
            peer_id,
            {**theirs, "lp": int(theirs.get("rp") or 0)},
        )
        return {"me": _me_wire_entry()}
    if op == "STATE":
        pid = os.getpid()
        p = mesh_dir() / f"{pid}-state.json"
        with contextlib.suppress(Exception):
            return {"digest": json.loads(p.read_text())[:4000] if False else json.loads(p.read_text())}
        return {"digest": {}}
    if op == "STATUS":
        return {"text": status()[:4000]}
    if op in ("DM", "BROADCAST", "ASK_REPLY"):
        line = (
            f"{_stamp()}|{req.get('from_id', '?')}|{req.get('name') or 'remote'}|"
            f"{('REPLY ' + str(req.get('qid') or '')) if op == 'ASK_REPLY' else ''}"
            f"{str(req.get('text') or '')[:400]}\n"
        )
        with contextlib.suppress(OSError):
            _wire_inbox().parent.mkdir(parents=True, exist_ok=True)
            with _wire_inbox().open("a", encoding="utf-8") as f:
                f.write(line)
        return {}
    if op == "ASK":
        import uuid

        qid = str(req.get("qid") or uuid.uuid4().hex[:8])
        rec = {
            "qid": qid,
            "from": req.get("from_id", "?"),
            "question": str(req.get("question") or "")[:400],
            "at": time.time(),
        }
        with contextlib.suppress(OSError):
            _wire_asks().parent.mkdir(parents=True, exist_ok=True)
            with _wire_asks().open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return {"qid": qid}
    return {}  # the wire already refused unknown ops


def _wire_route(op: str, ref: str, **fields) -> str | None:
    """Send one op to a REMOTE peer by ref. Returns a result string when
    routed, None when the ref is local (caller uses the v1 file path)."""
    rec = None
    for p in peers(refresh_now=True):
        if p.get("remote") and (
            ref.lower() in str(p.get("peer_id", "")).lower() or ref.lower() in str(p.get("summary", "")).lower()
        ):
            rec = p
            break
    if rec is None:
        return None
    from suijin.modules.agent.lib import mesh_wire

    try:
        reply = mesh_wire.request(
            "127.0.0.1",
            int(rec.get("lp") or 0),
            mesh_key(),
            op,
            from_id=f"node-{os.getpid()}",
            **fields,
        )
        return json.dumps(reply.get("digest") or reply.get("text") or {"ok": True}, indent=1, default=str)[:4000]
    except Exception as e:  # noqa: BLE001
        return f"Error: remote peer unreachable ({e}) — the tunnel may have died; /connect again"


def _start_wire() -> None:
    from suijin.modules.agent.lib.mesh_wire import MeshWireServer

    srv = MeshWireServer(mesh_key(), {"__any__": _handle_wire_dispatch})
    srv.start()
    _node["wire_server"] = srv
    if _node["me"] is not None:
        _node["me"]["port"] = srv.port
        _write_me(_node["me"])


def _handle_wire_dispatch(req: dict) -> dict:
    """One indirection so the handler table is a single callable."""
    return _handle_wire(req)


# ── cross-node asks (mesh_ask / mesh_reply) ─────────────────────────────


def ask_peer(ref: str, question: str) -> str:
    """Queue ONE question to a remote peer (budget: one outstanding per
    peer until answered or the TTL passes)."""
    q = str(question or "").strip().replace("\n", " ")
    if not q:
        return "Error: empty question"
    rec = next(
        (
            p
            for p in peers(refresh_now=True)
            if p.get("remote")
            and (ref.lower() in str(p.get("peer_id", "")).lower() or ref.lower() in str(p.get("summary", "")).lower())
        ),
        None,
    )
    if rec is None:
        # local peers answer through the group chat, not the ask queue
        return "Error: mesh_ask is for REMOTE peers (use mesh_dm for local nodes)"
    peer_id = rec.get("peer_id")
    out = _node.setdefault("asks_out", {})
    prev = out.get(peer_id)
    if prev and time.time() - float(prev.get("at") or 0) < ASK_TTL_S:
        return (
            f"Error: an ask to {peer_id} is already outstanding "
            f"(qid {prev.get('qid')}, {int(time.time() - float(prev.get('at') or 0))}s ago) — one at a time per peer"
        )
    import uuid

    from suijin.modules.agent.lib import mesh_wire

    qid = uuid.uuid4().hex[:8]
    try:
        mesh_wire.request(
            "127.0.0.1",
            int(rec.get("lp") or 0),
            mesh_key(),
            "ASK",
            from_id=f"node-{os.getpid()}",
            qid=qid,
            question=q,
        )
    except Exception as e:  # noqa: BLE001
        return f"Error: ask not delivered ({e})"
    out[peer_id] = {"qid": qid, "at": time.time(), "question": q}
    return f"ask queued to {peer_id} (qid {qid}) — the reply arrives in your mesh chat; mesh_reply sends it"


def reply_to_ask(qid: str, text: str) -> str:
    """Answer an ask WE received (qid from the MESH_ASK block)."""
    qid = str(qid or "").strip()
    t = str(text or "").strip().replace("\n", " ")
    if not qid or not t:
        return "Error: usage mesh_reply <qid> <answer>"
    sender = None
    with contextlib.suppress(OSError):
        for ln in _wire_asks().read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                rec = json.loads(ln)
                if rec.get("qid") == qid:
                    sender = rec.get("from")
    if sender is None:
        return f"Error: no ask with qid {qid} in this session's inbox"
    rec = next((p for p in _remote_peers().values() if p.get("peer_id") == sender), None)
    if rec is None:
        return f"Error: asker {sender} is no longer connected"
    from suijin.modules.agent.lib import mesh_wire

    try:
        mesh_wire.request(
            "127.0.0.1",
            int(rec.get("lp") or 0),
            mesh_key(),
            "ASK_REPLY",
            from_id=f"node-{os.getpid()}",
            qid=qid,
            text=t,
        )
    except Exception as e:  # noqa: BLE001
        return f"Error: reply not delivered ({e})"
    # answered: remove from the inbox so it is not re-served
    with contextlib.suppress(OSError):
        lines = [
            ln
            for ln in _wire_asks().read_text(encoding="utf-8", errors="replace").splitlines()
            if json.loads(ln).get("qid") != qid
        ]
        _wire_asks().write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return f"reply sent to {sender} for qid {qid}"


def drain_asks() -> list[str]:
    """Unanswered asks → context lines (untrusted-wrapped by the caller).
    Also expires OUR outbound asks past the TTL."""
    out: list[str] = []
    with contextlib.suppress(OSError):
        for ln in _wire_asks().read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                rec = json.loads(ln)
                out.append(
                    f"MESH_ASK qid={rec.get('qid')} from={rec.get('from')}: "
                    f"{rec.get('question')} — answer with mesh_reply {rec.get('qid')} <answer>"
                )
    for peer_id, rec in list(_node.get("asks_out", {}).items()):
        if time.time() - float(rec.get("at") or 0) >= ASK_TTL_S:
            _node["asks_out"].pop(peer_id, None)
    return out
