"""Mesh wire v2 — the loopback JSON-lines protocol between mesh nodes.

Posture (v1 ruling, extended): LOOPBACK PLAINTEXT ONLY. No public binds,
no TLS — an SSH tunnel IS the transport security for remote peers, and
local peers talk through the filesystem by preference; the wire exists
for what files cannot cross. Every request carries the node key; a wrong
key is refused and the connection dropped.

Protocol: one JSON object per line, one reply per line.

    {"v": 1, "key": "...", "op": "HELLO", "me": {...}}
    → {"ok": true, "me": {...}}

Ops: HELLO (exchange registry entries), PING, STATE (the caller-readable
digest this node publishes), STATUS (the mesh_status text), DM (a
point-to-point message into our chat), BROADCAST (same, tagged), ASK (a
queued question into our inbox), ASK_REPLY (an answer, drains like a DM).

Everything received from the wire is PEER data — the caller wraps it in
the untrusted-injection boundary exactly like filesystem mesh content.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading

PROTOCOL_VERSION = 1
WIRE_TIMEOUT_S = 10.0
_MAX_LINE = 1 << 20  # 1MiB — a digest or chat line, never a file transfer


class WireError(Exception):
    """A refused/failed wire exchange. The message is safe to show."""


def _suppress():
    return contextlib.suppress(Exception)


def _read_line(rfile) -> dict:
    raw = rfile.readline(_MAX_LINE)
    if not raw:
        raise WireError("connection closed")
    if len(raw) >= _MAX_LINE:
        raise WireError("line too large — the wire carries messages, not files")
    try:
        obj = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as e:
        raise WireError(f"malformed wire line: {e}") from e
    if not isinstance(obj, dict):
        raise WireError("wire line is not an object")
    return obj


def _write_line(wfile, obj: dict) -> None:
    wfile.write((json.dumps(obj, default=str) + "\n").encode("utf-8"))
    wfile.flush()


class MeshWireServer:
    """A node's loopback listener. One thread per connection; each request
    is authenticated against the node key before its op dispatches."""

    def __init__(self, node_key: str, handlers: dict):
        """`handlers` maps op → fn(request) → reply-dict (minus ok). The
        node owns what each op DOES; the wire owns transport + auth."""
        self._key = str(node_key or "")
        self._handlers = handlers
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))  # loopback ONLY — never 0.0.0.0
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="mesh-wire", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with _suppress():
            self._sock.close()

    def _serve(self) -> None:
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn: socket.socket) -> None:
        conn.settimeout(WIRE_TIMEOUT_S)
        bad = 0
        with conn, conn.makefile("rb") as rfile, conn.makefile("wb") as wfile:
            while not self._stop.is_set():
                try:
                    req = _read_line(rfile)
                except (WireError, OSError, ValueError):
                    return
                reply = self._dispatch(req)
                try:
                    _write_line(wfile, reply)
                except OSError:
                    return
                bad = bad + 1 if reply.get("error") == "bad key" else 0
                if bad >= 3:  # a peer that keeps guessing is not a peer
                    return

    def _dispatch(self, req: dict) -> dict:
        if str(req.get("key") or "") != self._key:
            return {"ok": False, "error": "bad key"}
        op = str(req.get("op") or "").upper()
        fn = self._handlers.get(op)
        if fn is None:
            # a node may install ONE catch-all ("__any__") that owns every
            # op — the mesh module does: its handler switches on op itself
            fn = self._handlers.get("__any__")
        if fn is None:
            return {"ok": False, "error": f"unknown op {op!r}"}
        try:
            out = fn(req) or {}
            return {"ok": True, **out}
        except Exception as e:  # noqa: BLE001 — one op failing never kills the node
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def request(host: str, port: int, node_key: str, op: str, timeout_s: float = WIRE_TIMEOUT_S, **fields) -> dict:
    """One request → one reply. Raises WireError on transport/auth failure."""
    out = {"v": PROTOCOL_VERSION, "key": str(node_key or ""), "op": str(op).upper(), **fields}
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s) as conn:
            conn.settimeout(timeout_s)
            with conn.makefile("wb") as wfile, conn.makefile("rb") as rfile:
                _write_line(wfile, out)
                reply = _read_line(rfile)
    except OSError as e:
        raise WireError(f"unreachable {host}:{port} ({e.__class__.__name__})") from e
    if not reply.get("ok"):
        raise WireError(str(reply.get("error") or "refused"))
    return reply
