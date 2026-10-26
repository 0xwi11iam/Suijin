#!/usr/bin/env python3
"""
Mock auth engagement — a realistic target with a login wall, run locally.

MIRRORS A REAL ENGAGEMENT:
  - Target has a login form (POST /login with user/pass)
  - Protected endpoints return 401 without a session cookie
  - After login, a session cookie grants access to /api/profile, /api/admin
  - One endpoint has an IDOR (admin panel accessible with user session)

THE TEST FLOW (what the agent would do):
  1. Agent probes /api/profile → 401 → calls request_auth
  2. Browser pops up on the host → operator logs in (test/test123)
  3. Session captured → agent stores it via creds_add
  4. Agent uses http_request(credential=...) to access protected endpoints
  5. Agent finds the IDOR and catalogs it

Run this to start the mock target:
  python3 mock_auth_target.py
"""

import http.server
import json
import socketserver
import time
import urllib.parse
import uuid

PORT = 6789
HOST = "127.0.0.1"

# user database (the "real" accounts)
USERS = {
    "test": {"password": "test123", "role": "user", "id": 1001, "email": "test@example.com"},
    "admin": {"password": "admin123", "role": "admin", "id": 1, "email": "admin@example.com"},
}

# active sessions: cookie_value -> user_info
SESSIONS = {}

# the "IDOR" data — admin-only notes
ADMIN_NOTES = [
    {"id": 1, "author": "admin", "body": "SECRET-FLAG{idor_admin_note_9f3k2x}"},
    {"id": 2, "author": "admin", "body": "rotate the API key: sk-live-abc123xyz"},
]


class AuthTarget(http.server.BaseHTTPRequestHandler):
    """A realistic target: login form, session cookies, protected API."""

    def _session(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session" and v in SESSIONS:
                return SESSIONS[v]
        return None

    def _json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        session = self._session()

        if path == "/" or path == "/login":
            # the login page (HTML like a real app)
            html = """<!DOCTYPE html>
<html><head><title>Test App</title></head><body>
<h1>Test App Login</h1>
<form method="POST" action="/login">
  <input name="username" placeholder="username">
  <input name="password" type="password" placeholder="password">
  <button type="submit">Log in</button>
</form>
</body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        elif path == "/api/profile":
            if not session:
                self._json(401, {"error": "authentication required", "hint": "POST /login first"})
                return
            self._json(
                200,
                {
                    "id": session["id"],
                    "username": session["username"],
                    "role": session["role"],
                    "email": session["email"],
                },
            )

        elif path == "/api/admin/notes":
            if not session:
                self._json(401, {"error": "authentication required"})
                return
            # THE IDOR: any authenticated user can read admin notes
            # (should check role == "admin" but doesn't)
            self._json(200, {"notes": ADMIN_NOTES, "total": len(ADMIN_NOTES)})

        elif path == "/api/health":
            self._json(200, {"status": "ok"})

        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            params = urllib.parse.parse_qs(body)
            username = params.get("username", [""])[0]
            password = params.get("password", [""])[0]

            user = USERS.get(username)
            if user and user["password"] == password:
                session_token = uuid.uuid4().hex
                SESSIONS[session_token] = {
                    "id": user["id"],
                    "username": username,
                    "role": user["role"],
                    "email": user["email"],
                }
                self.send_response(302)
                self.send_header("Set-Cookie", f"session={session_token}; Path=/; HttpOnly")
                self.send_header("Location", "/api/profile")
                self.end_headers()
            else:
                self._json(401, {"error": "invalid credentials"})

        else:
            self._json(404, {"error": "not found"})

    def log_message(self, format, *args):
        print(f"  [{time.strftime('%H:%M:%S')}] {args[0] if args else ''}")


def main():
    server = socketserver.TCPServer((HOST, PORT), AuthTarget)
    print(f"Mock auth target running at http://{HOST}:{PORT}")
    print()
    print("  Login page:   http://%s:%d/login" % (HOST, PORT))
    print("  Protected:    http://%s:%d/api/profile  (401 without session)" % (HOST, PORT))
    print("  IDOR target:  http://%s:%d/api/admin/notes  (any user can read)" % (HOST, PORT))
    print()
    print("  Test accounts:")
    print("    user:  test / test123")
    print("    admin: admin / admin123")
    print()
    print("  The flag is in /api/admin/notes (requires any session):")
    print("  SECRET-FLAG{idor_admin_note_9f3k2x}")
    print()
    print("  Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
