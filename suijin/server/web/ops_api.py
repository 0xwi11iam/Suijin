"""The operations API: Suijin's own logic, readable and controllable from the console.

Everything the terminal control hub can show or do, over HTTP:

* ``GET  /api/ops/overview``                     the whole board in one call (polled)
* ``GET  /api/ops/engagements[/{id}[/events|/file]]``  every engagement and what it holds
* ``POST /api/ops/intents`` · ``DELETE /api/ops/intents/{id}``   the work queue
* ``POST /api/ops/agents/{id}/{action}``         pause · resume · stop · steer
* ``POST /api/ops/swarm/{action}``               the same, for a swarm or the whole fleet
* ``POST /api/ops/coordinator/start`` · ``/api/ops/swarm/stop-all``
* ``/api/ops/boxes/…``                           sandboxes, the warm pile, the template image

Reading never blocks on docker (box state comes from a background cache), and every section of
the overview is gathered on its own, so one failing source is reported instead of taking the
board down. Control uses the same bus topics the terminal publishes on; nothing here kills a
process directly except the documented "stop everything", which is ``swarm stop``.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from suijin.server.swarm import lifecycle
from suijin.server.web import box_status
from suijin.server.web.console_api import _clean_finding_title
from suijin.server.web.data_hub import redact

router = APIRouter(prefix="/api/ops", tags=["ops"])

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,159}$")
ENGAGEMENT_SKIP = {"home", "tmp"}  # the agent's userland and scratch: not browsable from the console
AGENT_ACTIONS = ("pause", "resume", "stop", "steer")
SWARM_ACTIONS = ("pause", "resume", "stop", "steer")
STEER_CAP = 500


# ── where things are ───────────────────────────────────────────────────────────────────────


def _cfg() -> Path:
    from suijin.server.platform.lib.workspace import WORKSPACE_DIR

    return Path(WORKSPACE_DIR)


def _rt() -> Path:
    base = _cfg()
    return base.parent if base.name == "config" else base


def _db() -> sqlite3.Connection | None:
    path = _rt() / "swarm" / "swarm.db"
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path), timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _tail(path: Path, lines: int = 40) -> list[str]:
    if not path.is_file():
        return []
    with contextlib.suppress(OSError):
        return path.read_text(errors="replace").splitlines()[-lines:]
    return []


def _bus() -> tuple[str, int] | None:
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus

        return discover_bus(verify=True)
    return None


def _publish(topic: str, payload: dict) -> bool:
    from suijin.server.web import console_api

    return bool(console_api._publish(topic, payload))


# ── reading ─────────────────────────────────────────────────────────────────────────────────


def _agents() -> list[dict]:
    """Every row of the agents table, with what the console derives from it."""
    conn = _db()
    if conn is None:
        return []
    now = time.time()
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM agents ORDER BY last_heartbeat DESC").fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    for row in rows:
        beat = float(row.get("last_heartbeat") or 0)
        row["hb_age"] = round(now - beat, 1) if beat else None
        row["alive"] = bool(beat and now - beat <= 30)
        row["boxed"] = "-sbx" in str(row.get("id") or "")
        row["lane"] = str(row.get("current_intent") or "idle")
        row["tier"] = next(
            (name for name in ("m1-leader", "m2-sup", "m3-worker") if str(row.get("id") or "").startswith(name)), ""
        )
    return redact(rows)


def _intents(limit: int = 200) -> list[dict]:
    conn = _db()
    if conn is None:
        return []
    now = time.time()
    try:
        rows = [
            dict(row)
            for row in conn.execute("SELECT * FROM intents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    for row in rows:
        created = float(row.get("created_at") or 0)
        row["age_s"] = round(now - created) if created else None
        row["held_s"] = round(now - float(row.get("claimed_at") or 0)) if row.get("claimed_at") else None
    return redact(rows)


def _counts(agents: list[dict], intents: list[dict]) -> dict:
    out = {
        "agents": len(agents),
        "alive": sum(1 for a in agents if a["alive"]),
        "boxed": sum(1 for a in agents if a["boxed"]),
        "paused": sum(1 for a in agents if a.get("status") == "paused"),
        "intents": len(intents),
        "open": 0,
        "running": 0,
        "blocked": 0,
        "done": 0,
    }
    for intent in intents:
        state = str(intent.get("state") or "open")
        out[state] = out.get(state, 0) + 1
    return out


def _findings_summary() -> dict:
    conn = _db()
    out = {"total": 0, "confirmed": 0, "by_severity": {}, "latest": []}
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT severity, status, title, created_at FROM findings ORDER BY created_at DESC"
        ).fetchall()
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    out["total"] = len(rows)
    for row in rows:
        severity = str(row["severity"] or "info")
        out["by_severity"][severity] = out["by_severity"].get(severity, 0) + 1
        if row["status"] == "confirmed":
            out["confirmed"] += 1
    out["latest"] = [
        {"severity": r["severity"], "status": r["status"], "title": _clean_finding_title(str(r["title"] or ""))[0]}
        for r in rows[:5]
    ]
    return out


def _coordinator(bus: tuple[str, int] | None) -> dict:
    log = _rt() / "logs" / "swarm-coordinator.log"
    return {
        "running": bool(bus),
        "bus": f"{bus[0]}:{bus[1]}" if bus else "",
        "log": _tail(log, 30),
        "log_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(log.stat().st_mtime)) if log.is_file() else "",
    }


def _fleet() -> dict:
    cfg: dict = {}
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.config_loader import load_config

        loaded = load_config()
        cfg = loaded if isinstance(loaded, dict) else {}
    intents = _intents()
    busy = any(str(i.get("state")) in ("open", "running") for i in intents)
    enabled = cfg.get("swarm_autopilot", True) not in (False, 0, "false", "off")
    routing = dict(cfg.get("model_routing") or {})
    m1, m2, m3 = (str(cfg.get(k) or "") for k in ("model_1", "model_2", "model_3"))
    return {
        "pause_flag": (_rt() / "mesh" / "pause-swarm").exists(),
        "autopilot": {
            "enabled": enabled,
            "blocked": busy,
            "note": "an open or running intent anywhere holds it" if busy else "",
        },
        "turn_cap": cfg.get("swarm_turn_cap", 0),
        "pool_target": cfg.get("sandbox_pool_size", (cfg.get("sandbox") or {}).get("pool_size", 0)),
        "models": {
            "leader": m1,
            "supervisor": m2 or m1,
            "worker": m3 or m2 or m1,
            "planner": routing.get("planner") or "",
            "worker_route": routing.get("worker") or "",
        },
        "operator_mode": cfg.get("operator_mode", "basic"),
    }


def _mesh() -> dict:
    out: dict = {"peers": [], "chat": []}
    with contextlib.suppress(Exception):
        from suijin.server.agent.lib import mesh

        out["peers"] = [dict(p) for p in mesh.peers() if isinstance(p, dict)]
    for line in _tail(_rt() / "mesh" / "gc.log", 50)[::-1]:
        parts = line.split("|")
        if len(parts) >= 3:
            out["chat"].append(
                {"ts": parts[0], "src": parts[1], "engagement": parts[2], "message": "|".join(parts[3:])[:400]}
            )
    return redact(out)


def _jobs() -> list[dict]:
    with contextlib.suppress(Exception):
        from suijin.server.tools.lib import job_registry

        rows = [{k: v for k, v in job.items() if not str(k).startswith("_")} for job in job_registry.list_jobs()]
        return redact(sorted(rows, key=lambda r: r.get("started_at") or 0, reverse=True))
    return []


def _daemons() -> list[dict]:
    with contextlib.suppress(Exception):
        from suijin.server.ops.lib import daemon

        return redact([dict(r) for r in (daemon._records() or []) if isinstance(r, dict)])
    return []


def _browser_daemon() -> dict:
    out: dict = {"running": False, "port": None}
    with contextlib.suppress(Exception):
        from suijin.server import browser_daemon

        port = browser_daemon.read_port()
        out["port"] = port
        out["running"] = bool(port and browser_daemon._alive(port))
    return out


def _system() -> dict:
    """The latest background sample (see data_stores._system_stats): never a blocking read."""
    from suijin.server.web import data_stores

    return (data_stores._system_stats() or [{}])[0]


def _members(value) -> list[dict]:
    """The registry keeps a team as ``{role: [agent ids]}``; a plain list of ids has no roles."""
    rows: list[dict] = []
    items = value.items() if isinstance(value, dict) else [("", value)] if isinstance(value, list) else []
    for role, members in items:
        for member in members if isinstance(members, list) else [members]:
            if member:
                rows.append({"role": str(role), "id": str(member)})
    return rows


def _structured() -> list[dict]:
    """Leader / supervisor / worker teams, one registry file per engagement."""
    rows = []
    for file in sorted((_rt() / "swarm").glob("structured-*.json")) if (_rt() / "swarm").is_dir() else []:
        with contextlib.suppress(OSError, ValueError):
            tree = json.loads(file.read_text(errors="replace"))
            if isinstance(tree, dict):
                rows.append(
                    {
                        "slug": str(tree.get("slug") or file.name),
                        "file": file.name,
                        "leader": str(tree.get("leader") or ""),
                        "supervisors": _members(tree.get("supervisors")),
                        "workers": _members(tree.get("workers")),
                        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(file.stat().st_mtime)),
                    }
                )
    return rows


def _sessions() -> list[dict]:
    with contextlib.suppress(Exception):
        from suijin.server.web import console_api

        return redact(console_api.sessions_list().get("sessions") or [])
    return []


def overview() -> dict:
    """The whole board. Each section is gathered on its own; one that fails is named in ``errors``."""
    errors: dict[str, str] = {}
    board: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def section(name: str, build) -> None:
        try:
            board[name] = build()
        except Exception as error:  # noqa: BLE001 — report, never fail the board
            errors[name] = f"{type(error).__name__}: {str(error)[:160]}"

    bus = _bus()
    section("coordinator", lambda: _coordinator(bus))
    section("agents", _agents)
    section("intents", _intents)
    section("fleet", _fleet)
    section("findings", _findings_summary)
    section("mesh", _mesh)
    section("jobs", _jobs)
    section("daemons", _daemons)
    section("browser", _browser_daemon)
    section("system", _system)
    section("sessions", _sessions)
    section("structured", _structured)
    section("boxes", box_status.snapshot)
    section("image_build", box_status.image_build_state)
    board["counts"] = _counts(board.get("agents") or [], board.get("intents") or [])
    alive = {str(agent.get("id")): bool(agent.get("alive")) for agent in board.get("agents") or []}
    for team in board.get("structured") or []:
        ids = [
            team["leader"],
            *(member["id"] for member in team["supervisors"]),
            *(member["id"] for member in team["workers"]),
        ]
        team["live"] = sum(1 for member in ids if alive.get(member))
    board["errors"] = errors
    return board


@router.get("/overview")
def ops_overview() -> dict:
    return overview()


# ── engagements ─────────────────────────────────────────────────────────────────────────────


def _engagement_ids() -> list[str]:
    ids = set()
    root = _rt() / "engagements"
    if root.is_dir():
        ids.update(f.name for f in root.iterdir() if f.is_dir() and not f.name.startswith("."))
    with contextlib.suppress(Exception):
        from suijin.server.swarm import registry
        ids.update(str(row.get("id")) for row in registry.list_swarms() if row.get("id"))
    return sorted(ids, reverse=True)


def _valid_engagement(engagement_id: str) -> Path | None:
    if not SAFE_ID.match(engagement_id or "") or engagement_id not in _engagement_ids():
        return None
    with contextlib.suppress(Exception):
        from suijin.server.swarm import registry
        found = registry.find_folder(engagement_id, legacy_root=_rt() / "engagements")
        if found is not None:
            return found
    return _rt() / "engagements" / engagement_id


def _listing(folder: Path) -> list[dict]:
    rows = []
    if folder.is_dir():
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or ENGAGEMENT_SKIP & set(path.relative_to(folder).parts[:-1]):
                continue
            stat = path.stat()
            rows.append(
                {
                    "path": str(path.relative_to(folder)),
                    "bytes": stat.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                }
            )
    return rows


def engagement_detail(engagement_id: str) -> dict | JSONResponse:
    folder = _valid_engagement(engagement_id)
    if folder is None:
        return JSONResponse({"error": "no such engagement"}, status_code=404)
    meta: dict = {}
    if (folder / "engagement.json").is_file():
        with contextlib.suppress(OSError, ValueError):
            meta = json.loads((folder / "engagement.json").read_text())
    events = folder / "events.jsonl"
    kinds: dict[str, int] = {}
    lines = 0
    agents: dict[str, int] = {}
    if events.is_file():
        with contextlib.suppress(OSError):
            for line in events.read_text(errors="replace").splitlines():
                lines += 1
                with contextlib.suppress(ValueError):
                    record = json.loads(line)
                    kind = str(record.get("kind") or "?")
                    kinds[kind] = kinds.get(kind, 0) + 1
                    agent = str(record.get("agent") or "")
                    if agent:
                        agents[agent] = agents.get(agent, 0) + 1
    audit: dict = {}
    for path in sorted((folder / "audit_trails").glob("*.json")) if (folder / "audit_trails").is_dir() else []:
        with contextlib.suppress(OSError, ValueError):
            trail = json.loads(path.read_text())
            audit[str(path.name)] = {
                key: trail.get(key)
                for key in ("started", "ended", "total_actions", "successful_actions", "failed_actions", "cost_usd")
            }
    catalog = folder / "exploits" / "catalog.json"
    exploits: list[dict] = []
    if catalog.is_file():
        with contextlib.suppress(OSError, ValueError):
            for exp_id, entry in ((json.loads(catalog.read_text()) or {}).get("entries") or {}).items():
                attempts = entry.get("attempts") or []
                exploits.append(
                    {
                        "id": exp_id,
                        "title": entry.get("title"),
                        "class": entry.get("class"),
                        "severity": entry.get("severity"),
                        "cvss": entry.get("cvss"),
                        "status": entry.get("status"),
                        "attempts": len(attempts),
                        "last_verdict": attempts[-1].get("verdict")
                        if attempts and isinstance(attempts[-1], dict)
                        else "",
                    }
                )
    findings: list[dict] = []
    conn = _db()
    if conn is not None:
        try:
            findings = redact(
                [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id, title, severity, status, created_at FROM findings WHERE engagement = ? ORDER BY created_at DESC",
                        (engagement_id,),
                    ).fetchall()
                ]
            )
            for row in findings:  # old rows carry the cataloguer's frontmatter; the Findings page strips it too
                row["title"], row["exp_id"], _band, row["cvss"] = _clean_finding_title(str(row.get("title") or ""))
        except sqlite3.Error:
            findings = []
        finally:
            conn.close()
    state = folder / "state"
    return {
        "id": engagement_id,
        "meta": redact(meta),
        "kind": str(meta.get("kind") or ("legacy" if not meta else "engagement")),
        "events": {
            "lines": lines,
            "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
            "agents": dict(sorted(agents.items(), key=lambda kv: -kv[1])),
        },
        "inbox": _listing(folder / "inbox"),
        "reports": _listing(folder / "reports"),
        "notes": _listing(folder / ".notes"),
        "exploits": exploits,
        "exploit_files": _listing(folder / "exploits"),
        "state": _listing(state),
        "audit": audit,
        "findings": findings,
        "files": _listing(folder)[:400],
    }


@router.get("/engagements")
def ops_engagements() -> dict:
    from suijin.server.web import data_stores

    rows = data_stores._engagement_rows()
    live: dict[str, int] = {}
    for agent in _agents():
        if agent["alive"]:
            live[str(agent.get("swarm") or "")] = live.get(str(agent.get("swarm") or ""), 0) + 1
    for row in rows:
        row["live_agents"] = live.get(row["id"], 0)
    return {"engagements": redact(rows)}


@router.get("/engagements/{engagement_id}")
def ops_engagement(engagement_id: str):
    return engagement_detail(engagement_id)


@router.get("/engagements/{engagement_id}/events")
def ops_engagement_events(
    engagement_id: str, kind: str | None = None, offset: int = 0, limit: int = 100, q: str | None = None
):
    from suijin.server.web import data_hub

    if _valid_engagement(engagement_id) is None:
        return JSONResponse({"error": "no such engagement"}, status_code=404)
    result = data_hub.read(f"engagement.{engagement_id}.events", offset=offset, limit=limit, query=q, kind=kind)
    result["kinds"] = data_hub.kinds(f"engagement.{engagement_id}.events")
    return result


@router.get("/engagements/{engagement_id}/file")
def ops_engagement_file(engagement_id: str, name: str = ""):
    from suijin.server.web import data_hub

    if _valid_engagement(engagement_id) is None or not name:
        return JSONResponse({"error": "no such file"}, status_code=404)
    result = data_hub.read(f"engagement.{engagement_id}.files", name=name, limit=1)
    if result["rows"] and "error" in result["rows"][0]:
        return JSONResponse({"error": "no such file"}, status_code=404)
    return result


@router.get("/agents/{agent_id}/log")
def ops_agent_log(agent_id: str, lines: int = 200):
    if not SAFE_ID.match(agent_id or ""):
        return JSONResponse({"error": "bad agent id"}, status_code=400)
    path = Path(tempfile.gettempdir()) / f"suijin-agent-{agent_id}.log"
    if not path.is_file():
        return {"agent": agent_id, "lines": [], "note": "no log file for this agent"}
    return {
        "agent": agent_id,
        "path": f"<temp>/suijin-agent-{agent_id}.log",
        "lines": _tail(path, max(1, min(lines, 1000))),
    }


# ── control ─────────────────────────────────────────────────────────────────────────────────


@router.post("/intents")
def ops_add_intent(body: dict | None = None):
    """Queue work for a swarm — the terminal's ``swarm intent``: appended, never replaced, with the
    same prior-work gate, and broadcast so an idle agent picks it up at once."""
    body = body or {}
    text = str(body.get("text") or "").strip()
    swarm = str(body.get("swarm") or "default").strip() or "default"
    force = bool(body.get("force"))
    if not text:
        return JSONResponse({"error": "intent text required"}, status_code=400)
    if len(text) > 4000:
        return JSONResponse({"error": "intent text is over 4000 characters"}, status_code=400)
    note = ""
    with contextlib.suppress(Exception):
        from suijin.server.swarm.prior_work import gate_intent

        allowed, note = gate_intent(text, swarm, force=force)
        if not allowed:
            return JSONResponse({"error": str(note), "gate": str(note)}, status_code=409)
    from suijin.server.swarm import lifecycle

    intent_id, open_count = lifecycle._add_intent(text, swarm)
    _publish(f"/engagement/{swarm}/work", {"event": "new_intent", "text": text[:400], "swarm": swarm})
    return {"ok": True, "id": intent_id, "open": open_count, "gate": str(note) if note else ""}


@router.delete("/intents/{intent_id}")
def ops_drop_intent(intent_id: str):
    """Remove an intent nobody has picked up yet. A claimed one is refused: it is being worked."""
    if not SAFE_ID.match(intent_id or ""):
        return JSONResponse({"error": "bad intent id"}, status_code=400)
    conn = _db()
    if conn is None:
        return JSONResponse({"error": "no swarm database yet"}, status_code=404)
    try:
        cur = conn.execute("DELETE FROM intents WHERE id = ? AND state = 'open' AND agent_id IS NULL", (intent_id,))
        conn.commit()
        removed = max(0, cur.rowcount or 0)
    except sqlite3.Error as error:
        return JSONResponse({"error": str(error)[:160]}, status_code=500)
    finally:
        conn.close()
    if not removed:
        return JSONResponse({"error": "that intent is already claimed or finished"}, status_code=409)
    return {"ok": True, "removed": removed}


def _set_status(swarm: str | None, agent: str | None, status: str) -> None:
    """Mirror the terminal's pause/resume in the roster, the way the chat routes do."""
    conn = _db()
    if conn is None:
        return
    try:
        if agent:
            conn.execute("UPDATE agents SET status = ? WHERE id = ?", (status, agent))
        else:
            conn.execute("UPDATE agents SET status = ? WHERE swarm = ?", (status, swarm or "default"))
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


@router.post("/agents/{agent_id}/mission")
def ops_agent_mission(agent_id: str, body: dict | None = None):
    body = body or {}
    mission = str(body.get("mission") or "").strip()[:STEER_CAP]
    if not SAFE_ID.match(agent_id or ""):
        return JSONResponse({"error": "bad agent id"}, status_code=400)
    if not mission:
        return JSONResponse({"error": "a mission is required"}, status_code=400)
    sent = _publish("/cmd/mission", {"mission": mission, "agent": agent_id})
    return {
        "ok": sent,
        "agent": agent_id,
        "mission": mission,
        "note": "the mission is replaced at its next step"
        if sent
        else "the mesh bus did not accept the command — is the coordinator running?",
    }


@router.post("/agents/{agent_id}/{action}")
def ops_agent_action(agent_id: str, action: str, body: dict | None = None):
    body = body or {}
    if action not in AGENT_ACTIONS:
        return JSONResponse({"error": f"unknown action {action!r}", "actions": list(AGENT_ACTIONS)}, status_code=400)
    if not SAFE_ID.match(agent_id or ""):
        return JSONResponse({"error": "bad agent id"}, status_code=400)
    message = str(body.get("message") or "").strip()[:STEER_CAP]
    if action == "pause":
        sent = _publish("/cmd/pause", {"agent": agent_id})
        _set_status(None, agent_id, "paused")
    elif action == "resume":
        sent = _publish("/cmd/resume", {"agent": agent_id})
        _set_status(None, agent_id, "active")
    elif action == "stop":
        sent = _publish("/agent/" + agent_id + "/kill", {}) or _publish("/cmd/stop", {"agent": agent_id})
    else:
        if not message:
            return JSONResponse({"error": "a message is required to steer"}, status_code=400)
        sent = _publish(f"/agent/{agent_id}/steer", {"message": message})
    return {
        "ok": sent,
        "agent": agent_id,
        "action": action,
        "note": "" if sent else "the mesh bus did not accept the command — is the coordinator running?",
    }


# Literal routes are declared BEFORE the parameterised /swarm/{action} and /agents/{id}/{action}:
# a path parameter declared first swallows every literal under it.
@router.post("/swarm/mission")
def ops_swarm_mission(body: dict | None = None):
    """Replace the standing mission of a swarm (or of every agent when no swarm is given)."""
    body = body or {}
    mission = str(body.get("mission") or "").strip()[:STEER_CAP]
    swarm = str(body.get("swarm") or "").strip() or None
    if swarm and not SAFE_ID.match(swarm):
        return JSONResponse({"error": "bad swarm name"}, status_code=400)
    if not mission:
        return JSONResponse({"error": "a mission is required"}, status_code=400)
    sent = _publish("/cmd/mission", {"mission": mission, **({"swarm": swarm} if swarm else {})})
    return {
        "ok": sent,
        "mission": mission,
        "swarm": swarm,
        "note": "the mission is replaced at each agent's next step"
        if sent
        else "the mesh bus did not accept the command — is the coordinator running?",
    }


def _spawn_agents(swarm: str, mission: str, host: int, boxed: int):
    """The spawn core (terminal ``swarm spawn`` and the swarm launcher share one path —
    the operator rule: never a second path). Returns (spawned, error) where error is a
    (payload, status) pair to answer with, or None."""
    addr = _bus()
    if not addr:
        return None, ({"error": "the coordinator is not running — start it first"}, 409)
    fits, why = lifecycle.spawn_capacity_check(host, boxed)
    if not fits:
        return None, ({"error": "not enough free memory for that many agents", "detail": why}, 409)
    spawned: dict[str, list] = {"host": [], "boxed": []}
    for _ in range(host):
        identity, pid = lifecycle._spawn_agent(addr[1], mission=mission, swarm=swarm)
        spawned["host"].append({"id": identity, "pid": pid})
    if boxed:
        from suijin.server.box import hypervisor as hyp

        if not box_status._engine_socket():
            return spawned, (
                {"error": "no container engine is running — boxed agents need OrbStack or Docker. Start it, then try again.", "spawned": spawned},
                409,
            )
        if not hyp.image_exists():
            return spawned, ({"error": "the sandbox image is not built — build it under Boxes first", "spawned": spawned}, 409)
        box_addr = hyp.coordinator_addr_for_boxes(addr[1])
        for _ in range(boxed):
            identity = f"agent-{int(time.time()) % 10000}-{os.urandom(2).hex()}-sbx"
            name = hyp.spawn_sandbox_agent(box_addr, identity, swarm, mission=mission)
            if name:
                spawned["boxed"].append({"id": identity, "box": name})
    box_status.snapshot()  # refreshes in the background; never waits on docker
    return spawned, None


@router.post("/swarm/spawn")
def ops_swarm_spawn(body: dict | None = None):
    """Start agents in a swarm — the terminal's ``swarm spawn``: host agents as real processes
    (what hunts use), boxed agents in sandboxes. Boxed agents need an engine that is ALREADY
    running; this never starts one."""
    body = body or {}
    swarm = str(body.get("swarm") or "default").strip() or "default"
    mission = str(body.get("mission") or "").strip()[:STEER_CAP]
    if not SAFE_ID.match(swarm):
        return JSONResponse({"error": "bad swarm name"}, status_code=400)
    try:
        host = max(0, min(8, int(body.get("host") or 0)))
        boxed = max(0, min(16, int(body.get("boxed") or 0)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "host and boxed must be numbers"}, status_code=400)
    if not host and not boxed:
        return JSONResponse({"error": "ask for at least one agent"}, status_code=400)
    spawned, error = _spawn_agents(swarm, mission, host, boxed)
    if error:
        return JSONResponse(error[0], status_code=error[1])
    return {
        "ok": True,
        "spawned": spawned,
        "note": f"{len(spawned['host'])} host and {len(spawned['boxed'])} boxed agent(s) started in {swarm}",
    }


# ── swarms as things (the registry, S0/S1 of the 2026-11-28 plan) ───────────────────────────


@router.get("/swarms")
def ops_swarms_list():
    """Every swarm — the new Targets layout plus the legacy engagement archive as one list."""
    from suijin.server.swarm import registry

    return {"swarms": registry.list_swarms()}


@router.get("/swarms/{swarm_id}")
def ops_swarm_get(swarm_id: str):
    from suijin.server.swarm import registry

    data = registry.load(swarm_id)
    if data is None:
        return JSONResponse({"error": "no such swarm"}, status_code=404)
    conn = _db()
    agents, intents = [], []
    if conn is not None:
        try:
            agents = [
                {"id": r[0], "status": r[1], "intent": r[2], "heartbeat_age_s": round(max(0.0, time.time() - (r[3] or 0)))}
                for r in conn.execute(
                    "SELECT id, status, current_intent, last_heartbeat FROM agents WHERE swarm = ? ORDER BY id", (swarm_id,)
                ).fetchall()
            ]
            intents = [
                {"id": r[0], "text": r[1], "state": r[2]}
                for r in conn.execute(
                    "SELECT id, text, state FROM intents WHERE swarm = ? AND state IN ('open', 'running') ORDER BY created_at LIMIT 50",
                    (swarm_id,),
                ).fetchall()
            ]
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return {"swarm": data, "agents": agents, "open_intents": intents}


@router.post("/swarms")
def ops_swarm_define(body: dict | None = None):
    """Define a swarm and (at once, when start is set) launch it: the coordinator comes up,
    the lanes queue as prioritised work, the agents spawn — one path, the registry's file
    is the truth."""
    body = body or {}
    target = str(body.get("target") or "").strip()
    title = str(body.get("title") or "").strip()
    if not target or not title:
        return JSONResponse({"error": "target and title are required"}, status_code=400)
    lanes = [str(x).strip() for x in (body.get("lanes") or []) if str(x).strip()][:16]
    try:
        host = max(0, min(8, int(body.get("host") or 0)))
        boxed = max(0, min(16, int(body.get("boxed") or 0)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "host and boxed must be numbers"}, status_code=400)
    from suijin.server.swarm import registry

    definition = registry.create(
        target,
        title,
        lanes=lanes,
        host_n=host,
        boxed_n=boxed,
        brain=body.get("brain") if isinstance(body.get("brain"), dict) else {},
        engine=str(body.get("engine") or "auto"),
        permissions=str(body.get("permissions") or ""),
        mode=str(body.get("mode") or ""),
        chat=str(body.get("chat") or ""),
    )
    swarm = definition["id"]
    start = bool(body.get("start")) and bool(host or boxed)
    # start=false, or no agents asked: define ONLY — the definition records what was
    # asked, nothing spawns, no coordinator wakes, no lane queues (the lane is work
    # FOR agents; queuing it with nobody to work it left ghosts behind, seen in the
    # journey 2026-11-29). Launch later from the dialog or /swarm/spawn.
    if not start:
        return {"ok": True, "swarm": definition,
                "note": "defined — no agents asked for yet, nothing launched"}
    note_parts: list[str] = []
    addr = _bus()
    if not addr:
        from suijin.server.swarm import lifecycle

        ready = lifecycle.start_coordinator()
        if not ready:
            return JSONResponse(
                {"error": "the coordinator did not come up — see the coordinator log", "swarm": definition},
                status_code=502,
            )
        note_parts.append("coordinator started")
    for lane in lanes:
        try:
            lifecycle._add_intent(lane, swarm)
        except Exception:  # the swarm db may not exist until an agent registers
            pass
    spawned, error = _spawn_agents(swarm, title, host, boxed)
    if error:
        return JSONResponse({**error[0], "swarm": definition}, status_code=error[1])
    definition = registry.set_status(swarm, "running") or definition
    note_parts.append(f"{len(spawned['host'])} host and {len(spawned['boxed'])} boxed agent(s) started")
    return {"ok": True, "swarm": definition, "spawned": spawned, "note": "; ".join(note_parts)}


@router.delete("/swarms/{swarm_id}")
def ops_swarm_delete(swarm_id: str):
    """Stop the swarm's agents, then remove the folder, the rows and the definition together —
    the delete lesson: a half-deleted swarm must never resurrect."""
    if not SAFE_ID.match(swarm_id or ""):
        return JSONResponse({"error": "bad swarm id"}, status_code=400)
    from suijin.server.swarm import registry

    _publish("/cmd/stop", {"swarm": swarm_id})
    _publish("/cmd/delete", {"swarm": swarm_id})
    conn = _db()
    if conn is not None:
        try:
            conn.execute("DELETE FROM agents WHERE swarm = ?", (swarm_id,))
            conn.execute("DELETE FROM intents WHERE swarm = ?", (swarm_id,))
            conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    removed = registry.delete(swarm_id)
    return {"ok": True, "folder_removed": removed, "note": "stopped, then removed — definition, folder and roster rows together"}


@router.post("/swarm/stop-all")
def ops_swarm_stop_all():
    """Declared BEFORE /swarm/{action}: a path parameter declared first swallows every literal
    under it, and "Stop everything" answered "unknown action 'stop-all'" while the coordinator
    stayed up (found by the browser journey, 2026-11-27 — the unit tests called the function)."""
    return stop_everything()


@router.post("/swarm/{action}")
def ops_swarm_action(action: str, body: dict | None = None):
    """A fleet-wide (or one swarm's) command. With no swarm every agent is addressed — the same
    as the terminal's ``swarm pause`` / ``swarm resume``."""
    body = body or {}
    if action not in SWARM_ACTIONS:
        return JSONResponse({"error": f"unknown action {action!r}", "actions": list(SWARM_ACTIONS)}, status_code=400)
    swarm = str(body.get("swarm") or "").strip() or None
    if swarm and not SAFE_ID.match(swarm):
        return JSONResponse({"error": "bad swarm name"}, status_code=400)
    message = str(body.get("message") or "").strip()[:STEER_CAP]
    scope = {"swarm": swarm} if swarm else {}
    flag = _rt() / "mesh" / "pause-swarm"
    if action == "pause":
        sent = _publish("/cmd/pause", scope)
        if not swarm:
            with contextlib.suppress(OSError):
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text(str(time.time()))
        _set_status(swarm, None, "paused")
        note = "agents finish their current step, then hold"
    elif action == "resume":
        sent = _publish("/cmd/resume", scope)
        _publish("/swarm/status", {"event": "resume"})
        if not swarm:
            with contextlib.suppress(OSError):
                flag.unlink(missing_ok=True)
        _set_status(swarm, None, "active")
        note = "agents continue from where they held"
    elif action == "stop":
        if swarm:
            sent = _publish("/cmd/stop", scope)
            _publish("/cmd/delete", scope)
            note = "the swarm's agents exit at their next step"
        else:
            return JSONResponse({"error": "stopping everything is /api/ops/swarm/stop-all"}, status_code=400)
    else:
        if not message:
            return JSONResponse({"error": "a message is required to steer"}, status_code=400)
        sent = _publish("/cmd/steer", {"message": message, **scope})
        note = "read at each agent's next step"
    return {
        "ok": sent,
        "action": action,
        "swarm": swarm,
        "note": note if sent else "the mesh bus did not accept the command — is the coordinator running?",
    }


@router.post("/coordinator/start")
def ops_coordinator_start():
    """Start the swarm coordinator from this interpreter's tree, and wait until it is ready."""
    if _bus():
        return {"ok": True, "started": False, "note": "the coordinator is already running"}
    from suijin.server.swarm import lifecycle

    ready = lifecycle.start_coordinator()
    return {
        "ok": ready,
        "started": True,
        "note": "" if ready else "it did not answer in time — see the coordinator log",
    }


def stop_everything() -> dict:
    """``swarm stop``: broadcast a stop, let agents finish their step, stop the coordinator, remove
    every sandbox and the pile. Engagement data lives on the host and is untouched."""
    from suijin.server.swarm import lifecycle

    with contextlib.redirect_stdout(io.StringIO()):
        lifecycle.run_swarm_stop_all()
    box_status.refresh_now()
    return {"ok": True, "note": "agents were told to stop and the coordinator is down; the pile is gone"}


# ── boxes ───────────────────────────────────────────────────────────────────────────────────


def _hyp():
    from suijin.server.box import hypervisor

    return hypervisor


def _sandbox_name(name: str) -> bool:
    return bool(re.match(r"^suijin-sbx-[A-Za-z0-9_.\-]{1,120}$", name or ""))


@router.get("/boxes")
def ops_boxes() -> dict:
    return box_status.snapshot()


@router.post("/boxes/refresh")
def ops_boxes_refresh() -> dict:
    return refresh_boxes()


def refresh_boxes() -> dict:
    return box_status.refresh_now()


@router.post("/boxes/summon")
def ops_boxes_summon(body: dict | None = None):
    """Summon idle boxes into the warm pile. ``n`` is the target size (0–32); it only ever adds."""
    body = body or {}
    try:
        n = max(0, min(32, int(body.get("n", 4))))
    except (TypeError, ValueError):
        return JSONResponse({"error": "n must be a number"}, status_code=400)
    if not box_status._engine_socket():
        return JSONResponse({"error": "no container engine is running"}, status_code=409)
    hyp = _hyp()
    log: list[str] = []
    created = hyp.pool_refill_to(n, on_log=log.append, engagement=str(body.get("engagement") or "default"))
    box_status.refresh_now()
    return {"ok": True, "created": created, "target": n, "log": log}
    body = body or {}


@router.post("/boxes/destroy-idle")
def ops_boxes_destroy_idle():
    """The opposite of summon: the pile and any stopped sandbox go; running agents are untouched."""
    if not box_status._engine_socket():
        return JSONResponse({"error": "no container engine is running"}, status_code=409)
    hyp = _hyp()
    log: list[str] = []
    destroyed = hyp.destroy_idle_boxes(on_log=log.append)
    box_status.refresh_now()
    return {"ok": True, "destroyed": destroyed, "log": log}


@router.post("/boxes/gc")
def ops_boxes_gc():
    """Remove every sandbox — agents and the pile. Engagement data lives on the host."""
    if not box_status._engine_socket():
        return JSONResponse({"error": "no container engine is running"}, status_code=409)
    result = _hyp().sandbox_gc()
    box_status.refresh_now()
    return {"ok": True, "removed": result.get("removed", []), "count": result.get("count", 0)}


@router.post("/boxes/sandboxes/{name}/stop")
def ops_sandbox_stop(name: str):
    if not _sandbox_name(name):
        return JSONResponse({"error": "not a sandbox"}, status_code=400)
    if not box_status._engine_socket():
        return JSONResponse({"error": "no container engine is running"}, status_code=409)
    ok = _hyp().sandbox_stop(name)
    box_status.refresh_now()
    return {"ok": ok, "name": name}


@router.get("/boxes/sandboxes/{name}/logs")
def ops_sandbox_logs(name: str, tail: int = 80):
    if not _sandbox_name(name):
        return JSONResponse({"error": "not a sandbox"}, status_code=400)
    if not box_status._engine_socket():
        return JSONResponse({"error": "no container engine is running"}, status_code=409)
    return {"name": name, "lines": _hyp().sandbox_logs(name, max(1, min(tail, 500))).splitlines()}


@router.get("/boxes/image-build")
def ops_image_build_state() -> dict:
    return box_status.image_build_state()


@router.post("/boxes/image-build")
def ops_image_build(body: dict | None = None):
    """Build (or rebuild) the sandbox template image in the background."""
    body = body or {}
    return box_status.start_image_build(force=bool(body.get("force")))
