"""suijin web — the web console (the ARTEX-feel surface).

One page, served by FastAPI, fed by the bus + the data layer. What the
terminal console does, in a browser: agent cards with badges, live
traffic, findings board, mesh chat, the engagement TUI stream — plus
the graph ARTEX users expect.

Architecture: same posture as the desktop gateway — binds 127.0.0.1,
per-boot token, read-mostly. The page is ONE file (no build step, no
npm, no React) that talks WebSocket + fetch. The server subscribes to
the bus and queries the data layer.

  suijin web              → http://localhost:8790
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="suijin web console")

# the console UI: the vendored build (server/web/console-ui/dist). The old
# embedded page stays as fallback when the UI hasn't been built yet.
from suijin.server.web.console_api import router as _console_router
from suijin.server.web.data_api import router as _data_router
from suijin.server.web.ops_api import router as _ops_router

# Every API router is included BEFORE the SPA catch-all below: a route declared after it never
# matches (the catch-all answers any path first), which is how /api/state and /api/swarms were
# dead for weeks — they answered the catch-all's 404.
app.include_router(_console_router)
app.include_router(_ops_router)
app.include_router(_data_router)

_UI_DIST = Path(__file__).resolve().parents[3] / "server" / "web" / "console-ui" / "dist"

from suijin.server.web.console_page import CONSOLE_HTML as _PAGE_HTML

PAGE = None  # marker; index() serves the built UI (fallback: _PAGE_HTML)


_state_cache: dict = {"at": 0.0, "value": None}
_STATE_TTL_S = 10.0


@app.get("/api/state")
def state():
    """One pull: the whole board (the data layer's ``all``). It shells out to docker, so it is
    served from a short cache — the fallback page polls it."""
    now = time.time()
    if _state_cache["value"] is None or now - _state_cache["at"] > _STATE_TTL_S:
        from suijin.server.data import query

        _state_cache.update(value=query("all"), at=now)
    return JSONResponse(_state_cache["value"])


@app.get("/api/swarms")
async def swarms():
    from suijin.server.data import query

    return JSONResponse(query("swarms"))


@app.get("/")
async def index():
    idx = _UI_DIST / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text())
    return HTMLResponse(_PAGE_HTML)


@app.get("/{spa_path:path}")
async def spa(spa_path: str):
    """SPA catch-all — client routes (/chats, /settings…) serve the shell."""
    if spa_path.startswith("api/") or spa_path.startswith("ws"):
        return JSONResponse({"error": "not found"}, status_code=404)
    candidate = _UI_DIST / spa_path
    if candidate.is_file():
        from fastapi.responses import FileResponse

        return FileResponse(candidate)
    idx = _UI_DIST / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text())
    return HTMLResponse(_PAGE_HTML)

# ── state: bus listener feeds a broadcast list ───────────────────

_listeners: list = []
_lock = threading.Lock()


def _stats_publisher():
    """System stats → /system/stats every 5s (the web console's system
    pane renders cpu/memory/disk without pulling)."""
    with contextlib.suppress(Exception):
        while True:
            from suijin.server.mesh.bus import BusClient, discover_bus

            addr = discover_bus()
            if addr:
                from suijin.server.tools.lib.system_stats import collect

                d = collect(interval=0.5)
                bc = BusClient()
                if bc.connect(addr[0], addr[1], "web-stats"):
                    bc.publish("/system/stats", d)
                    bc.close()
            time.sleep(5)


def _bus_listener():
    """Subscribe to everything; forward to WS listeners."""
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient, discover_bus

        while True:
            addr = discover_bus()
            if not addr:
                time.sleep(2)
                continue
            bc = BusClient()
            if not bc.connect(addr[0], addr[1], "web-console"):
                time.sleep(2)
                continue
            bc.subscribe("/engagement/**")  # events, findings, usage, ledger, dead
            bc.subscribe("/swarm/**")       # heartbeats, control
            bc.subscribe("/mesh/**")
            bc.subscribe("/system/**")        # chat
            while True:
                for msg in bc.drain(50):
                    with _lock:
                        dead = []
                        for i, (ws, q) in enumerate(_listeners):
                            try:
                                q.append(msg)
                            except Exception:
                                dead.append(i)
                        for i in reversed(dead):
                            _listeners.pop(i)
                time.sleep(0.2)
            bc.close()


threading.Thread(target=_bus_listener, daemon=True, name="web-bus").start()
threading.Thread(target=_stats_publisher, daemon=True, name="web-stats").start()


def _parent_pid(pid: int) -> int:
    import subprocess

    with contextlib.suppress(Exception):
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out) if out else -1
    return -1


def _cleanup_orphan_agents():
    """At web boot, sweep what a crash left behind (operator ruling 2026-11-17: no orphans).

    An orphan is a tracked agent that is still alive, has been re-parented to launchd (its parent
    died) AND has no coordinator to answer to any more. Agents whose coordinator is still up are
    not orphans: they keep working and this console simply picks them up again. Until 2026-11-27
    this function imported a name that did not exist inside a suppress, so it never ran."""
    import signal

    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus
        from suijin.server.web.console_api import _ws

        time.sleep(3)                       # let the bus settle before judging it
        p = _ws() / "swarm" / "console-agents.pids"
        if not p.exists():
            return
        pids = [int(x) for x in p.read_text().split() if x.strip().isdigit()]
        coordinator_up = bool(discover_bus(verify=True))
        alive, killed = [], 0
        for pid in pids:
            try:
                os.kill(pid, 0)
            except OSError:
                continue                    # already gone
            if not coordinator_up and _parent_pid(pid) == 1:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
            else:
                alive.append(pid)
        p.write_text(" ".join(str(x) for x in alive))
        if killed:
            print(f"  [console] swept {killed} orphaned agent(s) left by a crash")


def _resume_structured():
    """Re-arm structured-mode watchdogs after a server restart."""
    time.sleep(6)
    with contextlib.suppress(Exception):
        from suijin.server.swarm.structured import resume_watchdogs

        resume_watchdogs()


threading.Thread(target=_resume_structured, daemon=True, name="web-structured").start()
threading.Thread(target=_cleanup_orphan_agents, daemon=True, name="web-orphan-cleanup").start()


@app.post("/api/say")
async def say(body: dict):
    """Mesh speech from the web console — same delivery as the terminal:
    gc.log + the bus /cmd/steer."""
    msg = str(body.get("msg") or "").strip()
    if not msg:
        return JSONResponse({"ok": False, "error": "msg required"})
    delivered = False
    with contextlib.suppress(Exception):
        from suijin.server.agent.lib.mesh import mesh_dir

        import time as _t

        d = mesh_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "gc.log").open("a") as f:
            f.write(f"{_t.strftime('%m-%d %H:%M:%S')}|{os.getpid()}|operator|{msg[:400]}\n")
        delivered = True
    steered = False
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient, discover_bus

        addr = discover_bus()
        if addr:
            bc = BusClient()
            if bc.connect(addr[0], addr[1], "web-say"):
                bc.publish("/cmd/steer", {"message": msg[:500]})
                bc.close()
                steered = True
    return JSONResponse({"ok": True, "mesh": delivered, "swarm": steered})


@app.post("/api/ingest")
async def start_ingest():
    """Start the ingest proxy from the web console (background thread)."""
    import threading

    from suijin.server.tools.lib.ingest import run_ingest

    threading.Thread(target=run_ingest, kwargs={"port": 8080}, daemon=True).start()
    return JSONResponse({"ok": True, "port": 8080, "hint": "browser proxy → localhost:8080"})


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q: list = []
    with _lock:
        _listeners.append((websocket, q))
    try:
        while True:
            while q:
                msg = q.pop(0)
                await websocket.send_text(json.dumps({
                    "topic": msg.get("topic", ""),
                    "payload": msg.get("payload", {}),
                }))
            import asyncio

            await asyncio.sleep(0.2)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with _lock:
            with contextlib.suppress(ValueError):
                _listeners.remove((websocket, q))


def run_web(port: int = 8790) -> int:
    from suijin.server.platform.lib.memory_guard import start_guard as _memory_guard

    _memory_guard("web", enforce=False)  # monitor-only: never kill the console
    import uvicorn

    print(f"  suijin web console → http://localhost:{port}")
    print("  Ctrl+C to stop")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0
