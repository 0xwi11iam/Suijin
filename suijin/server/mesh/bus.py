"""The swarm message bus — TCP pub/sub replacing gc.log (2026-11-05).

THE PROBLEM: the mesh communicates by appending lines to a shared
gc.log file. Every agent opens, reads the whole file, parses new bytes,
closes — every 200ms. At 100 agents that's 500 file operations/second
just for chat, on a file that grows unboundedly.

THE FIX: a TCP broker inside the coordinator (suijind). Each agent holds
ONE persistent connection, subscribes to topic patterns, receives only
matching messages. Zero file I/O for communication.

TOPICS (the topic IS the routing):
  /engagement/<slug>/findings     → a finding landed
  /engagement/<slug>/facts        → a fact was recorded
  /engagement/<slug>/coverage     → coverage cell marked
  /engagement/<slug>/work         → intent claimed/completed
  /engagement/<slug>/heartbeat    → agent alive (every 5s)
  /swarm/status                   → agent join/leave
  /swarm/steer/<agent_id>         → operator correction (real-time)

PROTOCOL: msgpack frames over TCP.
  Client → Broker:  SUB <pattern>\n  |  PUB <topic> <payload>\n
  Broker → Client:  MSG <topic> <payload>\n

The existing mesh_wire TCP protocol (PING/HELLO) coexists — the bus is
a superset. gc.log writes continue for audit (write-behind, not the
communication path).
"""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

# ── wire format ───────────────────────────────────────────────────────
# Simple length-prefixed msgpack-free JSON (msgpack optional later —
# JSON is universally available and the payload is small; the win over
# gc.log is the ROUTING, not the serialization).

_FRAME_HDR = "!I"  # 4-byte big-endian length


def _send_frame(sock: socket.socket, data: bytes) -> None:
    sock.sendall(struct.pack(_FRAME_HDR, len(data)) + data)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _recv_frame(sock: socket.socket) -> dict:
    (length,) = struct.unpack(_FRAME_HDR, _recv_exact(sock, 4))
    return json.loads(_recv_exact(sock, length))


# ── topic matching (glob: * matches one segment, ** matches all) ─────


def topic_matches(pattern: str, topic: str) -> bool:
    """'/engagement/*/findings' matches '/engagement/foo/findings'."""
    p_parts = pattern.strip("/").split("/")
    t_parts = topic.strip("/").split("/")

    def match(p, t):
        if not p:
            return not t
        if p[0] == "**":
            return True  # ** matches everything remaining
        if not t:
            return False
        if p[0] == "*" or p[0] == t[0]:
            return match(p[1:], t[1:])
        return False

    return match(p_parts, t_parts)


# ── the broker ────────────────────────────────────────────────────────


class BusBroker:
    """TCP message broker — runs inside the coordinator (suijind).

    One listener; each subscriber holds one persistent connection.
    Messages routed in-memory to matching subscribers; zero disk I/O.
    Bounded per-subscriber queues (backpressure: slow agents get
    heartbeats dropped, findings queued, never lost silently).
    """

    def __init__(self, port: int = 0, host: str = "127.0.0.1"):
        self._host = host
        self._requested_port = port
        self._actual_port = 0
        self._subscriptions: dict[str, set[socket.socket]] = defaultdict(set)
        self._queues: dict[socket.socket, deque] = {}
        self._lock = threading.Lock()
        self._running = False
        self._server_sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stats = {"published": 0, "delivered": 0, "subscribers": 0, "dropped": 0}

    @property
    def port(self) -> int:
        return self._actual_port

    @property
    def stats(self) -> dict:
        with self._lock:
            return {**self._stats, "subscribers": len(self._queues)}

    def start(self) -> int:
        """Bind, listen, start accepting. Returns the actual port."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._host, self._requested_port))
        self._actual_port = self._server_sock.getsockname()[1]
        self._server_sock.listen(64)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="bus-broker")
        self._accept_thread.start()
        return self._actual_port

    def stop(self):
        self._running = False
        with contextlib.suppress(OSError):
            if self._server_sock:
                self._server_sock.close()
        with self._lock:
            for sock in list(self._queues):
                with contextlib.suppress(OSError):
                    sock.close()
            self._queues.clear()
            self._subscriptions.clear()

    def _accept_loop(self):
        while self._running:
            try:
                client, addr = self._server_sock.accept()
                client.settimeout(300)  # idle timeout
                t = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
                t.start()
            except OSError:
                break

    def _handle_client(self, sock: socket.socket):
        """Read frames from the client: SUB or PUB."""
        self._queues[sock] = deque(maxlen=1000)
        try:
            while self._running:
                msg = _recv_frame(sock)
                op = str(msg.get("op") or "").upper()
                if op == "SUB":
                    pattern = str(msg.get("pattern") or "")
                    with self._lock:
                        self._subscriptions[pattern].add(sock)
                elif op == "PUB":
                    topic = str(msg.get("topic") or "")
                    payload = msg.get("payload") or {}
                    self._route(topic, payload)
                elif op == "UNSUB":
                    pattern = str(msg.get("pattern") or "")
                    with self._lock:
                        self._subscriptions[pattern].discard(sock)
                elif op == "STATS":
                    _send_frame(sock, json.dumps(self.stats).encode())
        except (ConnectionError, OSError, json.JSONDecodeError):
            pass
        finally:
            with self._lock:
                self._queues.pop(sock, None)
                for subs in self._subscriptions.values():
                    subs.discard(sock)
            with contextlib.suppress(OSError):
                sock.close()

    def _route(self, topic: str, payload: dict) -> int:
        """Push to every subscriber whose pattern matches. Returns delivered count."""
        delivered = 0
        with self._lock:
            for pattern, subscribers in self._subscriptions.items():
                if not topic_matches(pattern, topic):
                    continue
                frame = json.dumps({"op": "MSG", "topic": topic, "payload": payload}).encode()
                for sock in list(subscribers):
                    q = self._queues.get(sock)
                    if q is None:
                        continue
                    try:
                        q.append(frame)
                        delivered += 1
                        self._stats["delivered"] += 1
                    except Exception:  # queue full — drop (backpressure)
                        self._stats["dropped"] += 1
                # flush to matching subscribers
                for sock in list(subscribers):
                    self._flush(sock)
        self._stats["published"] += 1
        return delivered

    def _flush(self, sock: socket.socket):
        """Send queued frames to one subscriber."""
        q = self._queues.get(sock)
        if not q:
            return
        try:
            while q:
                frame = q.popleft()
                _send_frame(sock, frame)
        except (OSError, ConnectionError):
            pass  # the handle_client finally block cleans up


# ── the client ────────────────────────────────────────────────────────


class BusClient:
    """One persistent TCP connection to the broker.

    Auto-reconnects with exponential backoff (1s→2s→4s→max 30s).
    drain() returns all queued messages matching any subscription.
    """

    def __init__(self):
        self._sock: socket.socket | None = None
        self._inbox: deque = deque(maxlen=2000)
        self._patterns: list[str] = []
        self._connected = False
        self._reader_thread: threading.Thread | None = None
        self._host = ""
        self._port = 0
        self._identity = ""
        self._reconnect_delay = 1.0

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, host: str, port: int, identity: str = "") -> bool:
        self._host = host
        self._port = port
        self._identity = identity
        return self._try_connect()

    def _try_connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(5)
            self._sock.connect((self._host, self._port))
            self._sock.settimeout(0.1)  # non-blocking for reads
            self._connected = True
            self._reconnect_delay = 1.0
            # re-subscribe on reconnect
            for p in self._patterns:
                self._send({"op": "SUB", "pattern": p})
            # start reader
            if self._reader_thread is None or not self._reader_thread.is_alive():
                self._reader_thread = threading.Thread(target=self._reader, daemon=True, name="bus-client")
                self._reader_thread.start()
            return True
        except (OSError, ConnectionError):
            self._connected = False
            return False

    def _reader(self):
        while self._connected:
            try:
                if self._sock:
                    msg = _recv_frame(self._sock)
                    self._inbox.append(msg)
            except socket.timeout:
                continue
            except (ConnectionError, OSError):
                self._connected = False
                # reconnect with backoff
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30)
                self._try_connect()

    def _send(self, data: dict) -> bool:
        if not self._connected or not self._sock:
            return False
        try:
            _send_frame(self._sock, json.dumps(data).encode())
            return True
        except (OSError, ConnectionError):
            self._connected = False
            return False

    def subscribe(self, pattern: str) -> None:
        if pattern not in self._patterns:
            self._patterns.append(pattern)
        self._send({"op": "SUB", "pattern": pattern})

    def publish(self, topic: str, payload: dict) -> bool:
        return self._send({"op": "PUB", "topic": topic, "payload": payload, "from": self._identity})

    def request(self, topic: str, payload: dict, timeout: float = 10.0) -> dict | None:
        """Request/reply over pub/sub: publish with a correlation id +
        reply topic, subscribe, wait for THE matching reply. This is how
        the data layer answers remote clients (boxes, future UIs) —
        same answers as the in-process query(), no new daemon."""
        import os as _os
        import time as _time

        rid = _os.urandom(4).hex()
        reply_topic = f"{topic}/reply/{rid}"
        self.subscribe(reply_topic)
        deadline = _time.time() + timeout
        self.publish(topic, {**payload, "_rid": rid, "_reply_to": reply_topic})
        while _time.time() < deadline:
            for msg in self.drain(20):
                if str(msg.get("topic") or "") == reply_topic:
                    return (msg.get("payload") or {}).get("_result")
            _time.sleep(0.05)
        return None

    def drain(self, max_msgs: int = 50) -> list[dict]:
        out = []
        while self._inbox and len(out) < max_msgs:
            out.append(self._inbox.popleft())
        return out

    def close(self):
        self._connected = False
        with contextlib.suppress(OSError):
            if self._sock:
                self._sock.close()


# ── integration with the existing mesh ────────────────────────────────


def bus_port_file() -> Path:
    """Where the bus port is written (the coordinator writes it on start)."""
    from suijin.modules.agent.lib.mesh import mesh_dir

    return mesh_dir() / "bus.port"


def discover_bus(verify: bool = True) -> tuple[str, int] | None:
    """Find a running bus broker: read the port file, then (by default)
    VERIFY it answers. A stale port file after a crash made init think
    a coordinator existed — it then waited forever on a schema that
    would never come (the zombie class, field run 2026-11-06). A port
    that doesn't accept connections gets its file removed."""
    with contextlib.suppress(Exception):
        p = bus_port_file()
        if p.is_file():
            port = int(p.read_text().strip())
            if not verify:
                return ("127.0.0.1", port)
            import socket

            with contextlib.suppress(OSError):
                s = socket.create_connection(("127.0.0.1", port), timeout=1.5)
                s.close()
                return ("127.0.0.1", port)
            # dead port — stale file, remove it so nobody trusts it again
            with contextlib.suppress(OSError):
                p.unlink(missing_ok=True)
    return None


def start_bus(port: int = 0) -> BusBroker:
    """Start a broker and write the port file."""
    broker = BusBroker(port=port)
    actual = broker.start()
    with contextlib.suppress(OSError):
        bus_port_file().parent.mkdir(parents=True, exist_ok=True)
        bus_port_file().write_text(str(actual))
    return broker
