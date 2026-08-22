"""Suijin desktop gateway — the typed API between the core and the Tauri app.

Architecture (locked): the desktop app contains ZERO agent code. It is a
client of this gateway, which is a console-module surface:

    [Tauri app — pure TypeScript]  <--WS /events + REST-->  [this gateway]  -->  [kernel ctx]

Security posture: a security tool's UI must not become a backdoor.
  - binds 127.0.0.1 by default (remote opt-in via --host, explicitly)
  - per-boot random bearer token: printed once at startup, required in
    the Authorization header / query for WS; connections without it 401
  - read-mostly by design: state/tool-catalog/engagement streams open;
    the only writes are explicit operator actions (approvals, answers,
    launching engagements) — never silent agent actions

OpenAPI: every route is typed; /openapi.json is the single source from
which the TypeScript client is generated — core and UI cannot drift
because the UI's types ARE the gateway's schema.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── models (module scope: OpenAPI -> generated TS types flow from these) ──


class Status(BaseModel):
    version: str
    units: int
    tools: int
    packs: int
    kb_docs: int
    kb_built: bool
    provider: str
    stealth: bool
    kg_backend: str


class ToolInfo(BaseModel):
    name: str
    owner: str
    description: str
    params: list[str]


class ApproveBody(BaseModel):
    action: str = Field(pattern="^(approve|deny)$")
    note: str = ""


class AnswerBody(BaseModel):
    answer: str


class EngageBody(BaseModel):
    objective: str
    target: str = ""
    template: str = ""
    max_cost_usd: float = 10.0


# ── app + auth ────────────────────────────────────────────────────────────


def create_app(token: str | None = None) -> FastAPI:
    """Build the gateway app. token=None -> generate (server mode)."""
    app = FastAPI(
        title="Suijin Gateway",
        version="1.0.0",
        description="Typed API between the Suijin core and desktop clients. Auth: Bearer <session token>.",
    )
    app.state.token = token or secrets.token_urlsafe(24)
    state = {"token": app.state.token}  # route closures read this
    # Tauri dev runs on a different origin; the token is the real gate.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(authorization: str = Header(default="")) -> None:
        supplied = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied, state["token"]):
            raise HTTPException(status_code=401, detail="invalid session token")

    # ── helpers: lazy ctx access (kernel boots on demand) ───────────

    def _ctx():
        """Kernel ctx for tool serving. CRITICAL: workspace is ALWAYS the
        platform WORKSPACE_DIR (suijin_agent) — an unanchored boot defaults
        to Path.cwd() and scatters outputs/audit_trails/payloads/reports
        into whatever directory the gateway was started from."""
        from suijin.kernel.controller import last_context

        ctx = last_context()
        if ctx is None:
            from pathlib import Path as _P

            from suijin.kernel import controller
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

            ctx, _rep = controller.boot(
                module_roots=[_P(__file__).resolve().parents[3]],
                workspace=WORKSPACE_DIR,
                quiet=True,
            )
        return ctx

    # ── REST: read ───────────────────────────────────────────────────

    @app.get("/api/status", response_model=Status)
    def status(_: None = Depends(require_token)) -> Status:
        import suijin as pkg

        ctx = _ctx()
        from suijin.modules.loader import discover_modules

        discover_modules()
        from suijin.modules.knowledge.lib.kb import kb_status

        kb = kb_status() or {}
        from suijin.modules.platform.lib.config_loader import load_config
        from suijin.modules.platform.lib.stealth import is_on
        from suijin.modules.redteam.lib.intel.kg_backend import backend_status

        cfg = load_config()
        mods = Path(__file__).resolve().parents[3]
        packs = sum(1 for d in mods.iterdir() if (d / "manifest.json").exists())
        return Status(
            version=pkg.__version__,
            units=_boot_count(),
            tools=len(ctx.tool_names()),
            packs=packs,
            kb_docs=int(kb.get("docs", 0)),
            kb_built=bool(kb.get("docs")),
            provider=str(cfg.get("provider", "deepseek")),
            stealth=is_on(cfg),
            kg_backend=backend_status().split(" ")[0],
        )

    def _boot_count() -> int:
        try:
            from suijin.kernel.controller import last_context

            return len(getattr(last_context(), "_booted_unit_ids", []) or [])
        except Exception:  # noqa: BLE001
            return 0

    @app.get("/api/tools", response_model=list[ToolInfo])
    def tools(_: None = Depends(require_token)) -> list[ToolInfo]:
        ctx = _ctx()
        out = []
        for name in ctx.tool_names():
            e = ctx._tools.get(name, {})
            out.append(
                ToolInfo(
                    name=name,
                    owner=e.get("owner", ""),
                    description=e.get("description", ""),
                    params=e.get("params", []),
                )
            )
        return out

    @app.get("/api/usage")
    def usage(_: None = Depends(require_token)) -> dict:
        from suijin.modules.providers.lib import get_usage

        return get_usage()

    @app.get("/api/findings")
    def findings(_: None = Depends(require_token)) -> dict:
        from suijin.modules.redteam.lib.intel import knowledge_graph as kg

        out = {}
        for t in kg.get_all_targets():
            out[t] = kg.get_constraints(t)
        return out

    @app.get("/api/spar")
    def spar(_: None = Depends(require_token)) -> dict:
        from suijin.modules.ops.lib.sparring import _score_volley

        return _score_volley()

    @app.get("/api/fireteam")
    def fireteam(_: None = Depends(require_token)) -> dict:
        """Live fireteam registry (file mirror written by the agent process)."""
        from suijin.modules.agent.lib.nodes.subagent_node import _snapshot, _state_path

        try:
            if _state_path().exists():
                import json as _json

                return _json.loads(_state_path().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — fall through to the in-memory snapshot
            pass
        return _snapshot()

    # ── REST: operator actions (the ONLY writes) ─────────────────────

    @app.get("/api/approvals")
    def approvals_list(_: None = Depends(require_token)) -> list[dict]:
        from suijin.modules.ops.lib.approvals import list_approvals

        return list_approvals()

    @app.post("/api/approvals/{item_id}")
    def approvals_decide(item_id: int, body: ApproveBody, _: None = Depends(require_token)) -> dict:
        from suijin.modules.ops.lib.approvals import decide, list_approvals

        msg = decide(item_id, body.action == "approve", note=body.note)
        hit = next((a for a in list_approvals() if a["id"] == item_id), None)
        if hit is None:
            raise HTTPException(404, msg or "no such approval")
        return hit

    @app.get("/api/questions")
    def questions_list(_: None = Depends(require_token)) -> list[dict]:
        return _questions_read()

    @app.post("/api/questions/{qid}")
    def questions_answer(qid: int, body: AnswerBody, _: None = Depends(require_token)) -> dict:
        items = _questions_read()
        hit = next((q for q in items if q["id"] == qid), None)
        if hit is None:
            raise HTTPException(404, "no such question")
        hit["answer"] = body.answer
        hit["answered"] = True
        _questions_write(items)
        return hit

    # ── engagement launch (operator action; runs detached) ──────────

    @app.post("/api/engage")
    async def engage(body: EngageBody, _: None = Depends(require_token)) -> dict:
        import subprocess

        from suijin.modules.ops.lib.engagement_templates import apply_template

        cfg = dict(max_cost_usd=body.max_cost_usd)
        if body.template:
            resolved = apply_template(body.template, body.target or body.objective)
            cfg.update(resolved.get("config", {}))
        proc = await asyncio.create_subprocess_exec(
            sys_executable(),
            str(Path(__file__).resolve().parents[1] / "_engage_worker.py"),
            "--objective",
            body.objective,
            "--target",
            body.target,
            "--config",
            json.dumps(cfg),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"started": True, "pid": proc.pid, "objective": body.objective}

    # ── WebSocket: the live stream ───────────────────────────────────

    @app.websocket("/events")
    async def events(ws: WebSocket, token: str = Query("")):
        if token != state["token"]:
            await ws.close(code=4401, reason="invalid token")
            return
        await ws.accept()

        async def pump() -> None:
            """Tail the audit JSONLs and push structured frames."""
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

            trails = WORKSPACE_DIR / "outputs" / "audit_trails"
            from suijin.modules.agent.lib.nodes.subagent_node import _state_path as _ft_path

            ft_path = _ft_path()
            offsets: dict[str, int] = {}
            last_cost = -1.0
            last_ft = ""
            while True:
                if trails.is_dir():
                    for f in sorted(trails.glob("*.jsonl")):
                        key = f.name
                        start = offsets.get(key, 0)
                        if f.stat().st_size < start:
                            start = 0  # rotated/truncated
                        with f.open("r", encoding="utf-8", errors="ignore") as fh:
                            fh.seek(start)
                            for line in fh:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    entry = json.loads(line)
                                except ValueError:
                                    continue
                                await ws.send_json({"kind": "step", "stream": key, "entry": entry})
                        offsets[key] = f.stat().st_size
                # cost ticker
                try:
                    from suijin.modules.providers.lib import get_usage

                    u = get_usage()
                    cost = round(float(u.get("est_cost_usd", 0.0)), 4)
                    if cost != last_cost:
                        last_cost = cost
                        await ws.send_json({"kind": "cost", "est_cost_usd": cost, "calls": u.get("calls", 0)})
                except Exception:  # noqa: BLE001
                    pass
                # approvals/questions snapshots (cheap, small)
                try:
                    from suijin.modules.ops.lib.approvals import list_approvals

                    await ws.send_json({"kind": "approvals", "items": list_approvals()})
                except Exception:  # noqa: BLE001
                    await ws.send_json({"kind": "approvals", "items": []})
                # fireteam snapshot (file mirror; skip if unchanged)
                try:
                    if ft_path.exists():
                        blob = ft_path.read_text(encoding="utf-8")
                        if blob != last_ft:
                            last_ft = blob
                            await ws.send_json({"kind": "fireteam", **json.loads(blob)})
                except Exception:  # noqa: BLE001
                    pass
                await ws.send_json({"kind": "questions", "items": _questions_read()})
                await asyncio.sleep(0.6)

        pump_task = None
        try:
            pump_task = asyncio.create_task(pump())
            while True:
                # client->server messages reserved (commands come via REST)
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            if pump_task is not None:
                pump_task.cancel()

    return app


def sys_executable() -> str:
    import sys

    return sys.executable


# ── HITL stores (shared with the agent loop via the same files) ──────────


def _ws_dir() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR / "outputs"


def _approvals_read() -> list[dict]:
    p = _ws_dir() / "approvals.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(errors="ignore").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _approvals_write(items: list[dict]) -> None:
    _ws_dir().mkdir(parents=True, exist_ok=True)
    (_ws_dir() / "approvals.jsonl").write_text("\n".join(json.dumps(i) for i in items) + "\n")


def _questions_read() -> list[dict]:
    p = _ws_dir() / "questions.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(errors="ignore").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def push_question(question: str) -> int:
    """Agent-side bridge: persist an ask_operator question where the
    gateway serves it; returns its id. Used when stdin isn't a TTY
    (detached/desktop engagements) so the operator can answer from
    the Approvals screen."""
    items = _questions_read()
    next_id = max((q.get("id", 0) for q in items), default=0) + 1
    items.append({"id": next_id, "question": question[:500], "answered": False, "asked_at": _now_iso()})
    _questions_write(items)
    return next_id


def fetch_answer(question_id: int, timeout_s: float = 600.0, poll_s: float = 1.0) -> str | None:
    """Poll for the operator's answer (gateway writes it). None on timeout."""
    import time as _t

    deadline = _t.monotonic() + timeout_s
    while _t.monotonic() < deadline:
        hit = next((q for q in _questions_read() if q.get("id") == question_id), None)
        if hit and hit.get("answered"):
            return str(hit.get("answer", "")).strip() or None
        _t.sleep(poll_s)
    return None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _questions_write(items: list[dict]) -> None:
    _ws_dir().mkdir(parents=True, exist_ok=True)
    (_ws_dir() / "questions.jsonl").write_text("\n".join(json.dumps(i) for i in items) + "\n")


def _discovery_path() -> Path:
    """Where running gateways advertise themselves (one-click connect).

    ~/.suijin/gateway.json — the desktop app's connect screen reads it to
    pre-fill host/port/token. Written on serve(), removed on clean exit.
    SECURITY: 0600, contains the session token; only readable by the
    local user (the gateway is localhost-bound by default anyway)."""
    return Path.home() / ".suijin" / "gateway.json"


def _write_discovery(host: str, port: int, token: str) -> None:
    try:
        p = _discovery_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"url": f"http://{host}:{port}", "token": token, "pid": __import__("os").getpid()}))
        p.chmod(0o600)
    except Exception:  # noqa: BLE001 — discovery is best-effort
        pass


def _clear_discovery() -> None:
    import contextlib

    with contextlib.suppress(Exception):
        _discovery_path().unlink(missing_ok=True)


def serve(host: str = "127.0.0.1", port: int = 7331, token: str | None = None, quiet: bool = False) -> None:
    """Run the gateway. Prints the session token ONCE and advertises in
    ~/.suijin/gateway.json (the desktop app's one-click connect reads it).

    Discovery cleanup is belt-and-braces: the app lifespan shutdown hook
    (uvicorn graceful exit), an atexit fallback, and SIGINT/SIGTERM
    handlers — a stale file points a desktop client at a dead port
    otherwise."""
    import atexit
    import signal as _signal

    import uvicorn

    app = create_app(token=token)

    @app.on_event("shutdown")
    def _cleanup() -> None:
        _clear_discovery()

    _write_discovery(host, port, app.state.token)
    atexit.register(_clear_discovery)

    def _term(signum, frame):  # noqa: ANN001
        import contextlib

        with contextlib.suppress(Exception):
            _clear_discovery()
        raise SystemExit(0)

    _signal.signal(_signal.SIGTERM, _term)
    if not quiet:
        print(f"[suijin-gateway] http://{host}:{port}  token={app.state.token}", flush=True)
    try:
        uvicorn.run(app, host=host, port=port, log_level="error")
    finally:
        _clear_discovery()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="suijin-gateway")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7331)
    ap.add_argument("--token", default=None, help="fixed token (sidecar mode); default: random per boot")
    args = ap.parse_args()
    serve(host=args.host, port=args.port, token=args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
