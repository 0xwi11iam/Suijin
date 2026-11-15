"""suijin api — the explicit, keyed, read-only API (operator spec 2026-11-14).

  suijin api start [--port 8741]

- NEVER auto-started: only `suijin api start` (or run_api) brings it up
- obscure default port (8741 — not 8000/8080)
- API key REQUIRED on EVERY route, health checks included: X-API-Key
  header or ?key= param. The key is generated on first start and lives
  in ~/.suijin/config/api_key.json (local-only, never committed)
- READ-ONLY: the data layer + structured/registry state. Nothing here
  mutates the swarm. Strangers must not query the knowledge graph —
  the knowledge graph is not exposed at all.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="suijin api", docs_url=None, redoc_url=None, openapi_url=None)

DEFAULT_PORT = 8741


def _key_file() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR) / "api_key.json"
    return Path.home() / ".suijin" / "config" / "api_key.json"


def get_or_create_key() -> str:
    f = _key_file()
    with contextlib.suppress(Exception):
        key = json.loads(f.read_text()).get("key")
        if key:
            return key
    key = secrets.token_urlsafe(32)
    with contextlib.suppress(OSError):
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "created": time.time()}))
        tmp.replace(f)
        with contextlib.suppress(OSError):
            os_chmod(f)
    return key


def os_chmod(f: Path) -> None:
    import os

    os.chmod(f, 0o600)


def _check_key(x_api_key: str | None, key_param: str | None) -> None:
    expected = get_or_create_key()
    provided = x_api_key or key_param
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="API key required (X-API-Key header)")


@app.get("/health")
@app.get("/api/health")
def health(x_api_key: str | None = Header(default=None), key: str | None = None):
    """Even health requires the key (operator spec: EVERYTHING)."""
    _check_key(x_api_key, key)
    from suijin.server.mesh.bus import discover_bus

    return {"ok": True, "service": "suijin api", "swarm_live": bool(discover_bus())}


@app.get("/api/state")
def state(x_api_key: str | None = Header(default=None), key: str | None = None):
    _check_key(x_api_key, key)
    from suijin.server.data import query

    return JSONResponse(query("all"))


@app.get("/api/data/{q}")
def data_q(q: str, x_api_key: str | None = Header(default=None), key: str | None = None,
           engagement: str = "_default"):
    _check_key(x_api_key, key)
    from suijin.server.data import query

    return JSONResponse(query(q, engagement=engagement))


@app.get("/api/structured")
def structured(x_api_key: str | None = Header(default=None), key: str | None = None):
    _check_key(x_api_key, key)
    with contextlib.suppress(Exception):
        from suijin.server.swarm.structured import status_structured

        return {"ok": True, "structured": status_structured()}
    return {"ok": True, "structured": []}


@app.get("/api/boxes")
def boxes(x_api_key: str | None = Header(default=None), key: str | None = None):
    _check_key(x_api_key, key)
    from suijin.server.data import query

    return JSONResponse(query("boxes"))


@app.get("/api/ledger")
def ledger(target: str = "", x_api_key: str | None = Header(default=None), key: str | None = None):
    _check_key(x_api_key, key)
    rows = []
    with contextlib.suppress(Exception):
        from suijin.server.tools.lib.dead_regions import ledger_rows

        rows = ledger_rows(target=target)
    return {"ok": True, "rows": rows}


def run_api(port: int = DEFAULT_PORT) -> int:
    import uvicorn

    key = get_or_create_key()
    print(f"  suijin api → http://127.0.0.1:{port}  (key required on every route)")
    print(f"  key file: {_key_file()}  — X-API-Key header or ?key=")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0
