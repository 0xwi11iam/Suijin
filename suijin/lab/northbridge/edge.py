"""edge — the public gateway (stdlib http, :6000).

Everything the outside world touches first: routes to the services,
serves the marketing site, and carries the DEFENSES:
  - token-bucket rate limiting (429 + honest Retry-After)
  - WAF-cloak: scanner UAs and obvious probe paths get 404s that look
    exactly like real 404s (the citadel doctrine)
  - canary endpoints that flag scanners in telemetry
  - X-Internal stripped from ALL inbound requests (the m5 gate's
    outer wall)
  - security headers + CSP on every response

Planted (m1): a verbose upstream hint header + a services comment in
the marketing HTML that is PARTIALLY WRONG (names a cache that doesn't
exist, omits admin) — recon signal, not a giveaway.
Routes: /auth/* -> auth, /api/* -> core-api, /files/* -> objects.
admin and worker are NOT routed — internal only.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from suijin.lab.northbridge import PORT_ADMIN, PORT_AUTH, PORT_CORE, PORT_EDGE, PORT_OBJECTS, emit

ROUTES = {
    "/auth": ("127.0.0.1", PORT_AUTH),
    "/api": ("127.0.0.1", PORT_CORE),
    "/files": ("127.0.0.1", PORT_OBJECTS),
}

MARKETING = """<!doctype html>
<html><head><meta charset="utf-8"><title>Northbridge — tenant analytics</title></head>
<body>
<h1>Northbridge</h1>
<p>Analytics your finance team actually reads. Docs at /api/v1/ping (JSON).</p>
<!-- platform: edge, auth, core-api, objects, cache (redis), worker -->
<!-- customer files: /files/public/... — support sends presigned links -->
</body></html>
"""

#: m1: the verbose tell — real deployments leak worse
UPSTREAM_HINT = "core-api/2.4.1 (upstream fastapi)"

BUCKETS: dict[str, list[float]] = {}
RATE_LIMIT, RATE_WINDOW = 40, 10.0

SCANNER_UA = re.compile(r"sqlmap|nikto|nmap|masscan|zgrab|gobuster|dirbuster|wfuzz|acunetix", re.I)
CANARY_PATHS = {"/wp-login.php", "/wp-admin", "/.env", "/phpmyadmin", "/admin/config.php", "/.aws/credentials"}
PROBE_404S = re.compile(r"/(etc/passwd|c:/windows|boot\.ini|web\.config)$")

#: honest content: the decoy admin (citadel inheritance)
DECOY_PAGE = json.dumps({"admin": True, "token": "FLAG{northbridge_decoy_admin_bypass}", "note": "access granted"})


def _rate_ok(key: str) -> bool:
    now = time.time()
    hits = [t for t in BUCKETS.get(key, []) if now - t < RATE_WINDOW]
    BUCKETS[key] = hits
    if len(hits) >= RATE_LIMIT:
        return False
    hits.append(now)
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Upstream", UPSTREAM_HINT)  # m1
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        """Defense stack. Returns False when the request was answered."""
        ua = self.headers.get("User-Agent", "")
        if SCANNER_UA.search(ua):
            emit("canary_scanner_ua", ua[:80])
            self._send(404, b'{"error":"not found"}')  # indistinguishable 404
            return False
        if self.path in CANARY_PATHS:
            emit("canary_scanner_path", self.path)
            self._send(404, b'{"error":"not found"}')
            return False
        if PROBE_404S.search(self.path):
            emit("canary_probe", self.path[:80])
            self._send(404, b'{"error":"not found"}')
            return False
        key = f"{self.client_address[0]}:{self.path.split('?')[0]}"
        if not _rate_ok(key):
            self._send(429, b'{"error":"rate limited"}', extra={"Retry-After": "10"})
            return False
        return True

    def _proxy(self, method: str) -> None:
        for prefix, (host, port) in ROUTES.items():
            if self.path.startswith(prefix):
                # classic gateway: strip the route prefix — services own
                # their paths WITHOUT it (auth /login, core /v1/..., objects /public/...)
                url = f"http://{host}:{port}" + self.path[len(prefix) :]
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else None
                req = urllib.request.Request(url, data=body, method=method)
                for h in ("Content-Type", "Authorization", "Cookie", "User-Agent"):
                    if self.headers.get(h):
                        req.add_header(h, self.headers[h])
                # DEFENSE: the internal wall — no inbound request may
                # carry service credentials
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = resp.read()
                        ctype = resp.headers.get("Content-Type", "application/json")
                        # forward the upstream's own headers (CORS etc.)
                        fwd = {
                            k: v
                            for k, v in resp.headers.items()
                            if k.lower() not in ("content-length", "content-type", "server", "date", "connection")
                        }
                        return self._send(resp.status, data, ctype, extra=fwd)
                except urllib.error.HTTPError as e:
                    return self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
                except Exception:  # noqa: BLE001
                    return self._send(502, b'{"error":"upstream unavailable"}')
        if self.path == "/health":
            return self._send(200, b'{"ok":true,"service":"edge"}')
        if self.path == "/" or self.path.startswith("/index"):
            return self._send(200, MARKETING.encode(), "text/html")
        if self.path == "/admin":  # the DECOY (validates false, grades zero)
            emit("decoy_admin_hit", "")
            return self._send(200, DECOY_PAGE.encode())
        return self._send(404, b'{"error":"not found"}')

    def do_GET(self):  # noqa: N802
        if self._guard():
            self._proxy("GET")

    def do_POST(self):  # noqa: N802
        if self._guard():
            self._proxy("POST")

    def do_PUT(self):  # noqa: N802
        if self._guard():
            self._proxy("PUT")


def run() -> None:
    ThreadingHTTPServer(("127.0.0.1", PORT_EDGE), Handler).serve_forever()


if __name__ == "__main__":
    run()
