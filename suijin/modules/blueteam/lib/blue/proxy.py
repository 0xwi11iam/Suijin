"""
suijin/core/blue/proxy.py — Transparent HTTP forward proxy.

Starts a lightweight HTTP server that intercepts all traffic, logs every request
to a JSONL file for the blue team monitor, and forwards to the real target app.
No middleware needed — just point your browser/curl at the proxy port.

Usage:
    from suijin.modules.blueteam.lib.blue.proxy import start_proxy
    from suijin.modules.platform.lib.constants import _proxy_default_port(), _blue_lab_port(), _blue_traffic_log()
    proxy = start_proxy(listen_port=_proxy_default_port(), target_port=_blue_lab_port(),
                        log_path=str(_blue_traffic_log()))
    # All traffic to :8080 gets logged then forwarded to :5906
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen


def _blue_lab_port():
    from suijin.modules.platform.lib.constants import BLUE_LAB_PORT as _v

    return _v


def _blue_tarpit_file():
    from suijin.modules.platform.lib.constants import BLUE_TARPIT_FILE as _v

    return _v


def _blue_traffic_log():
    from suijin.modules.platform.lib.constants import BLUE_TRAFFIC_LOG as _v

    return _v


def _proxy_default_port():
    from suijin.modules.platform.lib.constants import PROXY_DEFAULT_PORT as _v

    return _v


class ProxyHandler(BaseHTTPRequestHandler):
    """Handles incoming HTTP requests — logs them, forwards them, returns the response."""

    target_host = "127.0.0.1"
    target_port = _blue_lab_port()
    log_path = str(_blue_traffic_log())

    def _log_request(self, method, path, headers, body):
        """Write request to JSONL log for the blue team."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": method,
            "path": path,
            "query": {},
            "body": body if body else "",
            "ip": self.client_address[0],
            "user_agent": headers.get("User-Agent", ""),
            "headers": {
                k: v
                for k, v in headers.items()
                if k.lower()
                in ("content-type", "cookie", "authorization", "x-forwarded-for", "x-admin", "origin", "referer")
            },
        }
        # Parse query string from path
        if "?" in path:
            from urllib.parse import parse_qs

            path_part, qs = path.split("?", 1)
            entry["path"] = path_part
            entry["query"] = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(qs).items()}

        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _forward(self, method, path, headers, body):
        """Forward request to target app using thread pool to avoid blocking."""
        from concurrent.futures import ThreadPoolExecutor

        target_url = f"http://{self.target_host}:{self.target_port}{path}"
        req_headers = {k: v for k, v in headers.items() if k.lower() not in ("host", "content-length")}
        req_headers["Host"] = f"{self.target_host}:{self.target_port}"
        req_headers["Connection"] = "close"
        data = body.encode("utf-8") if body else None

        def _do_request():
            req = Request(target_url, data=data, headers=req_headers, method=method)
            return urlopen(req, timeout=30)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_request)
                resp = future.result(timeout=30)
            resp_body = resp.read()
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in ("transfer-encoding", "connection", "keep-alive"):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(resp_body)
        except URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Proxy error: target unreachable — {e}".encode())
        except Exception:
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _handle(self, method):
        """Handle any HTTP method."""
        path = self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace") if content_length > 0 else ""

        # Log the request
        self._log_request(method, path, dict(self.headers), body)

        # BF1: enforcement plane — blocks/honeypots/fakes/redirects/canaries
        # read per-request, applied HERE (instant effect, no app mutation)
        try:
            from suijin.modules.blueteam.lib.blue import enforcement

            action = enforcement.enforce(method, path, self.client_address[0], body)
            if action:
                if action["kind"] == "block":
                    self.send_response(403)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"403 blocked by defensive policy\n")
                elif action["kind"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", action["url"])
                    self.end_headers()
                else:  # respond (honeypot / fake)
                    payload = action["body"].encode()
                    self.send_response(action.get("status", 200))
                    self.send_header("Content-Type", action.get("content_type", "text/plain"))
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                return
        except Exception:
            pass

        # Proxy-level tarpit: check if this IP should be delayed
        try:
            from suijin.modules.blueteam.lib.blue.defense.tarpit import delay_for

            wait = delay_for(self.client_address[0], path=_blue_tarpit_file())
            if wait > 0:
                time.sleep(wait)
        except Exception:
            pass

        # Forward to target
        self._forward(method, path, dict(self.headers), body)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_OPTIONS(self):
        self._handle("OPTIONS")

    # Suppress HTTP server log noise
    def log_message(self, format, *args):
        pass


class ProxyServer:
    """Manages the lifecycle of the forward proxy server."""

    def __init__(
        self,
        listen_port: int = _proxy_default_port(),
        target_port: int = _blue_lab_port(),
        target_host: str = "127.0.0.1",
        log_path: str = None,
    ):
        if log_path is None:
            log_path = str(_blue_traffic_log())
        self.listen_port = listen_port
        self.target_port = target_port
        self.log_path = log_path
        self._server: HTTPServer = None
        self._thread: threading.Thread = None

        # Configure the handler class
        ProxyHandler.target_host = target_host
        ProxyHandler.target_port = target_port
        ProxyHandler.log_path = log_path

    def start(self):
        """Start the proxy in a background thread."""
        self._server = HTTPServer(("0.0.0.0", self.listen_port), ProxyHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Shut down the proxy."""
        if self._server:
            self._server.shutdown()
            self._server = None

    @property
    def is_running(self) -> bool:
        return self._server is not None


def start_proxy(
    listen_port: int = _proxy_default_port(),
    target_port: int = _blue_lab_port(),
    target_host: str = "127.0.0.1",
    log_path: str = None,
) -> ProxyServer:
    if log_path is None:
        log_path = str(_blue_traffic_log())
    """Start a forward proxy — returns the ProxyServer instance."""
    proxy = ProxyServer(listen_port, target_port, target_host, log_path)
    proxy.start()

    # Verify it's actually listening
    for _ in range(10):
        time.sleep(0.1)
        try:
            s = socket.socket()
            s.settimeout(0.5)
            s.connect(("127.0.0.1", listen_port))
            s.close()
            return proxy
        except Exception:
            pass

    proxy.stop()
    raise RuntimeError(f"Proxy failed to start on port {listen_port}")
