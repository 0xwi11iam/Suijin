"""The data hub over HTTP — every piece of local Suijin data through one door.

* ``GET /api/data/catalog``                 every store, grouped, with live row counts
* ``GET /api/data/stores/{id}``             rows (``q`` free-text, ``kind``/``name``/``state``… exact filters)
* ``GET /api/data/stores/{id}/kinds``       the distinct record kinds of a journal
* ``WS  /api/data/stream``                  ``{"store", "rows", "at}`` whenever a store changes

The same door exists in Python for plugins and scripts::

    from suijin.server.web import data_hub
    data_hub.catalog(); data_hub.read("swarm.intents", state="open"); data_hub.watch(fn)

and on the mesh bus as ``/data/query`` (request) and ``/data/change`` (notification).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from suijin.server.web import data_hub

router = APIRouter(prefix="/api/data", tags=["data"])

FILTERS = ("kind", "name", "swarm", "state", "status", "severity", "id", "agent", "tier", "enabled", "target", "class")

SNIPPET = """from suijin.server.web import data_hub

stores = data_hub.catalog()                       # every store, with live row counts
agents = data_hub.read("swarm.agents")["rows"]    # rows, as plain dicts
open_work = data_hub.read("swarm.intents", state="open", limit=20)
events = data_hub.read("engagement.<slug>.events", kind="tool.call", limit=50)

def changed(store_id, payload):
    print(store_id, "now has", payload.get("rows"), "rows")

unwatch = data_hub.watch(changed)                 # also published on the mesh bus as /data/change
"""


@router.get("/catalog")
def data_catalog(group: str | None = None) -> dict:
    stores = data_hub.catalog(group)
    groups: dict[str, dict] = {}
    for store in stores:
        entry = groups.setdefault(store["group"], {"stores": 0, "rows": 0})
        entry["stores"] += 1
        entry["rows"] += int(store.get("rows") or 0)
    return {"stores": stores, "groups": [{"id": name, **info} for name, info in sorted(groups.items())]}


@router.get("/stores/{store_id}")
def data_store(request: Request, store_id: str) -> dict:
    params = request.query_params
    try:
        offset, limit = max(0, int(params.get("offset") or 0)), max(1, min(int(params.get("limit") or 100), 1000))
    except ValueError:
        return {"store": store_id, "error": "offset and limit must be numbers", "rows": []}
    exact = {key: params[key] for key in FILTERS if params.get(key) not in (None, "")}
    return data_hub.read(store_id, offset=offset, limit=limit, query=params.get("q"), **exact)


@router.get("/stores/{store_id}/kinds")
def data_store_kinds(store_id: str) -> dict:
    data_hub.refresh_engagements()
    if data_hub.get(store_id) is None:
        return {"store": store_id, "error": "no such store", "kinds": []}
    return {"store": store_id, "kinds": data_hub.kinds(store_id)}


@router.get("/snippet")
def data_snippet() -> dict:
    return {"python": SNIPPET}


@router.get("/bus")
def data_bus() -> dict:
    """How to reach the hub from another process."""
    return {
        "query": {
            "topic": "/data/query",
            "payload": {
                "q": ["swarm", "swarms", "boxes", "sessions", "engagement", "system", "all"],
                "engagement": "optional",
            },
            "reply": "{_rid, _result}",
        },
        "change": {"topic": "/data/change", "payload": "{store, rows, at}"},
    }


@router.websocket("/stream")
async def data_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    queue: list[dict] = []
    lock = threading.Lock()

    def changed(store_id: str, payload: dict) -> None:
        with lock:
            queue.append(payload)
            del queue[:-200]

    unwatch = data_hub.watch(changed)
    await websocket.send_text(json.dumps({"hello": True, "stores": len(data_hub.catalog())}))
    try:
        while True:
            with lock:
                batch, queue[:] = queue[:], []
            for payload in batch:
                await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, Exception):  # noqa: BLE001 — every disconnect ends the same way
        pass
    finally:
        with contextlib.suppress(Exception):
            unwatch()
