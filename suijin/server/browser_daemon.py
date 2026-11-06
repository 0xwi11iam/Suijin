"""browser daemon — ONE chromium, N agent contexts, over TCP.

Why: 15 agents each launching a browser = 15 × ~400MB = dead laptop.
One chromium (~400MB) + N contexts (~30MB each) = fits, and everyone
shares the same engine.

Protocol: JSON lines over TCP on 127.0.0.1 (loopback only — boxes
reach it via host.docker.internal like the bus). Each request:
  {"agent": "<identity>", "op": "goto"|"eval"|"requests"|"cookies"
              |"storage_state", "url": ..., "js": ..., "session": ...}
  → one JSON-line reply.

Contexts: one browser CONTEXT per agent identity (isolated cookies),
LRU-evicted past browser.max_contexts (default 8). A context is born
with the operator's saved session for its target when one exists
(suijin auth sessions — the cookie jar follows the agent).

Engine: patchright → playwright (capability check), chrome channel →
bundled chromium (the auth ladder, reused).

Start:  suijin browser daemon        (background, port file written)
Stop:   suijin browser daemon stop
Status: suijin browser daemon status
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

PORT_FILE_NAME = "browser-daemon.port"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def port_file() -> Path:
    from suijin.server.platform.lib.workspace import WORKSPACE_DIR

    d = Path(WORKSPACE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / PORT_FILE_NAME


def read_port() -> int | None:
    with contextlib.suppress(Exception):
        return int(port_file().read_text().strip())
    return None


def _max_contexts() -> int:
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        return max(1, int((load_config() or {}).get("browser", {}).get("max_contexts", 8)))
    return 8


class BrowserCore:
    """ONE worker thread owns ALL playwright usage (sync objects are
    single-thread — the field crash: contexts created in client threads
    died with TargetClosedError). Clients submit closures via queue;
    results come back through a condition variable."""

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._results: dict = {}
        self._cv = threading.Condition()
        self._seq = 0
        self.engine = "starting"
        self._contexts: "OrderedDict[str, object]" = OrderedDict()
        self._net: "OrderedDict[str, list]" = OrderedDict()
        self._browser = None
        self._pw = None
        threading.Thread(target=self._loop, daemon=True, name="browser-core").start()

    def _loop(self):
        with contextlib.suppress(Exception):
            from patchright.sync_api import sync_playwright as _pw

            stack = "patchright"
        with contextlib.suppress(Exception):
            if self._pw is None:
                from playwright.sync_api import sync_playwright as _pw

                stack = "playwright"
        self._pw = _pw().start()
        try:
            self._browser = self._pw.chromium.launch(channel="chrome", headless=True)
            self.engine = f"chrome via {stack}"
        except Exception:  # noqa: BLE001 — no real chrome on this host
            self._browser = self._pw.chromium.launch(headless=True)
            self.engine = f"chromium via {stack}"
        print(f"[browser-daemon] engine ready: {self.engine}", flush=True)
        while True:
            seq, fn = self._queue.get()
            try:
                out = {"ok": True, "result": fn()}
            except Exception as e:  # noqa: BLE001
                out = {"ok": False, "error": f"{e.__class__.__name__}: {str(e)[:200]}"}
            with self._cv:
                self._results[seq] = out
                self._cv.notify_all()

    def submit(self, fn, timeout: float = 60) -> dict:
        with self._cv:
            self._seq += 1
            seq = self._seq
        self._queue.put((seq, fn))
        deadline = time.time() + timeout
        with self._cv:
            while seq not in self._results and time.time() < deadline:
                self._cv.wait(0.3)
        return self._results.pop(seq, {"ok": False, "error": "browser op timed out"})

    def _context_for(self, agent: str, session: str = ""):
        key = f"{agent}@{session or 'default'}"
        if key in self._contexts:
            self._contexts.move_to_end(key)
            return self._contexts[key]
        while len(self._contexts) >= _max_contexts():
            old_key, old_ctx = next(iter(self._contexts.items()))
            with contextlib.suppress(Exception):
                old_ctx.close()
            self._contexts.pop(old_key, None)
            self._net.pop(old_key, None)
        kwargs = {"viewport": {"width": 1280, "height": 800}}
        sp = session or "default"
        sess_file = Path.home() / ".suijin" / "config" / "sessions" / f"{sp}.json"
        if sess_file.exists():
            kwargs["storage_state"] = str(sess_file)
        ctx = self._browser.new_context(**kwargs)
        self._contexts[key] = ctx
        self._net[key] = []
        page = ctx.new_page()
        page.on("request", lambda r: self._net[key].append(
            {"url": r.url, "method": r.method, "ts": time.time()}) if key in self._net else None
        )
        return ctx

    def handle(self, req: dict) -> dict:
        agent = str(req.get("agent") or "anon")[:40]
        op = str(req.get("op") or "")
        session = str(req.get("session") or "")
        key = f"{agent}@{session or 'default'}"

        def work():
            ctx = self._context_for(agent, session)
            pages = ctx.pages
            page = pages[0] if pages else ctx.new_page()
            if op == "goto":
                page.goto(str(req.get("url") or ""), timeout=25000)
                return {"url": page.url, "title": page.title()}
            if op == "eval":
                return {"result": str(page.evaluate(str(req.get("js") or "1")))[:1500]}
            if op == "requests":
                return {"requests": self._net.get(key, [])[-50:]}
            if op == "cookies":
                return {"cookies": [{"name": c["name"], "domain": c.get("domain", "")}
                                    for c in ctx.cookies()][:60]}
            if op == "storage_state":
                st = ctx.storage_state()
                return {"n_cookies": len(st.get("cookies", []))}
            return {}

        if op == "ping":
            return {"ok": True}
        if op == "info":
            return {"ok": True, "engine": self.engine, "contexts": len(self._contexts),
                    "max": _max_contexts()}
        if not op:
            return {"ok": False, "error": "op required"}
        out = self.submit(work, timeout=70)
        return out


def serve(host: str = "127.0.0.1", poll: float = 0.05) -> None:
    import socket

    core = BrowserCore()
    srv = socket.socket(socket.AF_INET)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, 0))
    port = srv.getsockname()[1]
    port_file().write_text(str(port))
    srv.listen(8)
    print(f"browser daemon on 127.0.0.1:{port} (port file: {port_file()})", flush=True)
    stop = {"flag": False}

    def _sig(_s, _f):
        stop["flag"] = True
        with contextlib.suppress(Exception):
            port_file().unlink()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    srv.settimeout(0.25)
    workers: list[threading.Thread] = []

    def _serve_client(conn):
        try:
            buf = b""
            conn.settimeout(60)
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    req = json.loads(line.decode(errors="replace"))
                    try:
                        resp = core.handle(req)
                    except Exception as e:  # noqa: BLE001 — one bad op never kills the daemon
                        resp = {"ok": False, "error": f"{e.__class__.__name__}: {str(e)[:200]}"}
                    conn.sendall((json.dumps(resp) + "\n").encode())
        except Exception:  # noqa: BLE001 — client hangup etc.
            pass
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    while not stop["flag"]:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        t = threading.Thread(target=_serve_client, args=(conn,), daemon=True)
        t.start()
        workers.append(t)
    with contextlib.suppress(Exception):
        port_file().unlink(missing_ok=True)


# ── process management (start/stop/status from the CLI) ─────────


def daemon_process() -> subprocess.Popen | None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.Popen(
        [sys.executable, "-c",
         "from suijin.server.browser_daemon import serve; serve()"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, env=env,
    )
    return p


def ensure_daemon(wait: float = 15.0) -> int | None:
    port = read_port()
    if port and _alive(port):
        return port
    port_file().unlink(missing_ok=True)
    daemon_process()
    deadline = time.time() + wait
    while time.time() < deadline:
        port = read_port()
        if port and _alive(port):
            return port
        time.sleep(0.3)
    return None


def _alive(port: int) -> bool:
    import socket

    with contextlib.suppress(Exception):
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.sendall((json.dumps({"op": "ping"}) + "\n").encode())
        s.settimeout(30)
        data = s.recv(4096)
        s.close()
        return b'"ok"' in data
    return False


def stop_daemon() -> bool:
    port = read_port()
    if not port:
        return True
    rc, out = subprocess.run(["pgrep", "-f", "browser_daemon import"], capture_output=True, text=True).stdout, ""
    pids = subprocess.run(["pgrep", "-f", "browser_daemon"], capture_output=True, text=True).stdout.split()
    for pid in pids:
        with contextlib.suppress(Exception):
            os.kill(int(pid), signal.SIGTERM)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not read_port():
            return True
        time.sleep(0.3)
    for pid in pids:
        with contextlib.suppress(Exception):
            os.kill(int(pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        port_file().unlink(missing_ok=True)
    return True


class BrowserClient:
    """Thin client — what dispatch tools call."""

    def __init__(self, timeout: float = 60):
        self.timeout = timeout

    def call(self, req: dict) -> dict:
        import socket

        port = ensure_daemon()
        if not port:
            return {"ok": False, "error": "browser daemon did not start"}
        s = socket.create_connection(("127.0.0.1", port), timeout=self.timeout)
        s.sendall((json.dumps(req) + "\n").encode())
        s.settimeout(self.timeout)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        with contextlib.suppress(Exception):
            return json.loads(buf.decode(errors="replace"))
        return {"ok": False, "error": "bad daemon response"}
