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
    """Register this session as a mesh node; start heartbeat + poll.

    Idempotent — a process is exactly one node."""
    if _node["me"] is not None:
        return
    mesh_dir().mkdir(parents=True, exist_ok=True)
    me = {"pid": os.getpid(), "summary": summary[:120], "phase": phase, "started": time.time()}
    _node["me"] = me
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
    """Unregister; sweep the runtime dir when the last node leaves."""
    with contextlib.suppress(Exception):
        (_node.get("stop") or threading.Event()).set()
        _me_file().unlink(missing_ok=True)
        if not list(mesh_dir().glob("*.json")):
            # last one out: chat is ephemeral by ruling — sweep it
            for p in (mesh_dir() / "gc.log", *mesh_dir().glob("dm-*.log")):
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
    read when asked for or when the cache is older than POLL_S."""
    now = time.time()
    if refresh_now or now - _peers["at"] >= POLL_S:
        live = []
        with contextlib.suppress(OSError):
            for f in mesh_dir().glob("*.json"):
                if f.name.endswith("-state.json"):
                    continue
                with contextlib.suppress(Exception):
                    rec = json.loads(f.read_text())
                    pid = int(rec.get("pid") or 0)
                    if pid and pid != os.getpid() and now - float(rec.get("beat") or 0) < STALE_S:
                        live.append(rec)
        _peers["list"] = live
        _peers["at"] = now
    return _peers["list"]


def refresh() -> None:
    peers(refresh_now=True)
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


def _tail(path: Path, offset: float) -> tuple[list[str], float]:
    """Lines appended to `path` since `offset` (mtime gate)."""
    with contextlib.suppress(OSError):
        mtime = path.stat().st_mtime
        if mtime <= offset:
            return [], offset
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        return lines, mtime
    return [], offset


_tail_state: dict = {"gc": 0.0, "dm": {}, "gc_lines": [], "dm_lines": []}


def _tail_reset() -> None:
    _tail_state["gc"] = 0.0
    _tail_state["dm"] = {}


def poll_chat() -> list[str]:
    """New GC + DM-to-me lines since the last poll, oldest first.

    Called once per think turn; the returned lines ride the context
    (untrusted-wrapped by the caller)."""
    out: list[str] = []
    gc_lines, _tail_state["gc"] = _tail(_gc_log(), _tail_state["gc"])
    out += [f"GC {ln}" for ln in gc_lines]
    dm_out = []
    for p in peers():
        pid = int(p.get("pid") or 0)
        if not pid:
            continue
        lines, off = _tail(_dm_file(pid), _tail_state["dm"].get(pid, 0.0))
        _tail_state["dm"][pid] = off
        # only lines addressed TO me (I tail pair files; my own writes are
        # excluded so a DM isn't echoed back to its sender)
        dm_out += [
            f"DM {ln}" for ln in lines if ln.split("|")[1:2] == [str(os.getpid())] or ln.split("|")[1:2] != [str(pid)]
        ]
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
        rows.append(f"node-{p.get('pid')}: {str(p.get('summary', ''))[:80]} (phase {p.get('phase', '?')})")
    rows.append(f"{len(ps) + 1} session(s) connected")
    return "\n".join(rows)
