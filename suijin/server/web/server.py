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

PAGE = Path(__file__).parent / "web_console.html"

# ── state: bus listener feeds a broadcast list ───────────────────

_listeners: list = []
_lock = threading.Lock()


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
            bc.subscribe("/engagement/**")
            bc.subscribe("/swarm/**")
            bc.subscribe("/mesh/**")
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


@app.get("/")
async def index():
    return HTMLResponse(PAGE.read_text() if PAGE.exists() else "<h1>suijin web</h1><p>page missing</p>")


@app.get("/api/state")
async def state():
    """One pull: the whole board (the data layer)."""
    from suijin.server.data import query

    return JSONResponse(query("all"))


@app.get("/api/swarms")
async def swarms():
    from suijin.server.data import query

    return JSONResponse(query("swarms"))


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
    import uvicorn

    print(f"  suijin web console → http://localhost:{port}")
    print("  Ctrl+C to stop")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0
