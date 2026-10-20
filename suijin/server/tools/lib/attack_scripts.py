"""Attack scripts (2026-10-20, CyberStrike attack_script adaptation).

Governed Python attack utilities with structured output — capabilities
beyond single HTTP replays. The flagship: ssrf_listener, a callback
listener that turns SSRF/oob hits into EVIDENCE (every hit logged with
timestamp + path + source; the log IS the finding's proof).

The listener is a daemon-threaded HTTP server on a local port. For
targets that must reach you over the internet, the operator fronts it
with a tunnel — the tool reports the address to embed.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _store_path() -> Path:
    from suijin.modules.platform.lib.workspace import engagement_dir

    return Path(engagement_dir()) / "state" / "ssrf_hits.jsonl"


def _append_hit(record: dict) -> None:
    with contextlib.suppress(Exception):
        p = _store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def _lan_ip() -> str:
    """Best-effort LAN address (the IP a same-network target would use)."""
    with contextlib.suppress(Exception):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no traffic sent — just picks a route
            return s.getsockname()[0]
        finally:
            s.close()
    return "127.0.0.1"


class _HitHandler(BaseHTTPRequestHandler):
    def _record(self):
        _append_hit(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "method": self.command,
                "path": self.path,
                "client": self.client_address[0],
                "ua": str(self.headers.get("User-Agent") or "")[:120],
                "server": self.server.server_address[1],
            }
        )
        body = b"ok"
        with contextlib.suppress(Exception):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _record

    def log_message(self, *a):  # silence — the jsonl store is the log
        pass


_LISTENERS: dict[int, ThreadingHTTPServer] = {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def ssrf_listener(action: str = "start", port: int = 0) -> str:
    """Callback listener for out-of-band evidence: SSRF, blind XSS,
    webhook/callback endpoints, cache validators.

    action=start (port optional, 0 = pick free): begins listening;
    returns the URL to embed in payloads (LAN IP — front it with a
    tunnel/cloud NAT for internet-reachable targets).
    action=hits: the recorded hits so far (the evidence).
    action=stop: shut the listener down.
    Every hit lands in the engagement state store (ssrf_hits.jsonl)."""
    try:
        act = str(action or "start").lower().strip()
        if act == "start":
            if _LISTENERS:
                ports = ", ".join(str(p) for p in _LISTENERS)
                return f"already listening on port(s) {ports} — use action=hits to read evidence, action=stop to end."
            p = int(port) if int(port or 0) > 0 else _free_port()
            srv = ThreadingHTTPServer(("0.0.0.0", p), _HitHandler)
            srv.daemon_threads = True
            t = threading.Thread(target=srv.serve_forever, daemon=True, name=f"ssrf-listener-{p}")
            t.start()
            _LISTENERS[p] = srv
            lan = _lan_ip()
            return (
                f"LISTENING on http://{lan}:{p} (any path — every hit is logged as evidence).\n"
                f"Embed URLs like http://{lan}:{p}/ssrf-probe in your payloads.\n"
                f"NOTE: this is your LAN address. For an internet-reachable target you need a "
                f"public address/tunnel (e.g. run one, then use action=stop + start on the tunnel's local end).\n"
                f"Read evidence with action=hits."
            )
        if act == "hits":
            p = _store_path()
            if not p.exists():
                return "no hits recorded yet."
            lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            if not lines:
                return "no hits recorded yet."
            hits = []
            for ln in lines[-50:]:
                with contextlib.suppress(ValueError):
                    h = json.loads(ln)
                    hits.append(
                        f"- [{h.get('ts')}] {h.get('method')} {h.get('path')} from {h.get('client')}"
                        f"{' ua=' + h['ua'] if h.get('ua') else ''}"
                    )
            n = len(lines)
            return f"SSRF/OOB EVIDENCE — {n} hit(s) total (showing last {len(hits)}):\n" + "\n".join(hits)
        if act == "stop":
            if not _LISTENERS:
                return "no listener running."
            for p, srv in list(_LISTENERS.items()):
                with contextlib.suppress(Exception):
                    srv.shutdown()
                    srv.server_close()
                del _LISTENERS[p]
            return "listener stopped. The hit log is preserved in the engagement state."
        return "Error: action must be start | hits | stop"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"
