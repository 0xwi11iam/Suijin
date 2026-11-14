"""suijin ingest — human traffic becomes the agent's asset graph.

The ARTEX-class unlock: a human browses the app (CAPTCHAs, OAuth,
multi-step checkouts — the things LLMs fumble), and every request
flows into the traffic index + asset graph. The agent then probes what
the HUMAN found, authenticated, without ever learning to log in.

  suijin ingest                    # starts a proxy on :8080
  suijin ingest --port 9090        # custom port

  # point your browser or Burp upstream at localhost:8080
  # browse; every request/response lands in the traffic index

Works two ways:
  1. BUILT-IN PROXY: a lightweight HTTP proxy (no mitmproxy dependency)
  2. MITMPROXY ADDON: if mitmproxy is installed, `mitmdump -s
     suijin/server/tools/lib/ingest_addon.py` feeds the same index
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def _ws_root() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).parent
    return Path.home() / ".suijin"


def record_traffic(method: str, url: str, status: int, req_headers: dict,
                   resp_headers: dict, body_preview: str = "") -> None:
    """One request/response pair into the traffic index (the same index
    the observation engine reads)."""
    with contextlib.suppress(Exception):
        from suijin.server.tools.lib.traffic_index import index_request

        index_request(
            url=url, method=method, status=status,
            req_headers=dict(req_headers), resp_headers=dict(resp_headers),
            body=body_preview[:2000],
        )
    # also append to the engagement's traffic.jsonl (durable, agent-visible)
    with contextlib.suppress(Exception):
        eng = _ws_root() / "engagements" / "_default"
        eng.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "method": method,
            "url": url,
            "status": status,
            "req_headers": {k: v for k, v in req_headers.items() if k.lower() not in ("authorization", "cookie")},
            "resp_headers": dict(resp_headers),
            "body_preview": body_preview[:500],
        }
        with (eng / "ingested_traffic.jsonl").open("a") as f:
            f.write(json.dumps(entry) + "\n")


class _ProxyHandler(BaseHTTPRequestHandler):
    """CONNECT-tunnel proxy: intercepts HTTP; passes HTTPS through with
    SNI visible (headers not decrypted without a CA — mitmproxy addon
    for that)."""

    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        port = int(port or 443)
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except Exception:
            self.send_error(502, f"cannot reach {self.path}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        # tunnel bytes both ways
        self.connection.setblocking(0)
        upstream.setblocking(0)
        conns = [self.connection, upstream]
        deadline = time.time() + 300
        while time.time() < deadline:
            import select

            r, _, _ = select.select(conns, [], [], 5)
            for s in r:
                other = upstream if s is self.connection else self.connection
                try:
                    data = s.recv(65536)
                    if not data:
                        return
                    other.sendall(data)
                except (BlockingIOError, InterruptedError):
                    pass
                except Exception:
                    return

    def _proxy_http(self):
        parsed = urlparse(self.path)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        url = f"http://{self.path}"
        # forward
        import http.client

        try:
            conn = http.client.HTTPConnection(host, port, timeout=15)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            conn.request(self.command, self.path, body=body, headers=dict(self.headers))
            resp = conn.getresponse()
            resp_body = resp.read()
            # record
            record_traffic(self.command, url, resp.status,
                           dict(self.headers), dict(resp.getheaders()),
                           resp_body[:500].decode("utf-8", "replace"))
            # relay
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as e:
            self.send_error(502, str(e))

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _proxy_http

    def log_message(self, format, *args):
        pass  # quiet — the traffic index IS the log


def run_ingest(port: int = 8080) -> int:
    from rich.console import Console

    console = Console()
    console.print(f"  [green]suijin ingest[/green] — proxy on :{port}")
    console.print(f"  point your browser/system proxy at [cyan]localhost:{port}[/cyan]")
    console.print("  every request lands in the traffic index + asset graph")
    console.print("  for HTTPS interception: [dim]mitmdump -s suijin/server/tools/lib/ingest_addon.py[/dim]")
    console.print("  Ctrl+C to stop\n")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _ProxyHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n  [dim]ingest stopped[/dim]")
    return 0
