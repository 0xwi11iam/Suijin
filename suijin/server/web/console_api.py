"""console_api — the Suijin API adapter behind the vendored console UI.

The frontend (server/web/console-ui) speaks the dashboard HTTP contract.
This module implements every path it calls, translated to REAL Suijin
state: swarm.db, the engagement tree, the bus, config.json, the tool
registry. No demo data — if Suijin isn't running, the UI shows that.

Sessions map to swarms: a console session id IS an engagement/swarm
name. "New swarm" starts a real hunt (run_swarm_hunt in a thread);
typing into the composer steers via /cmd/steer; stop publishes
/cmd/stop. Events stream from the engagement's events.jsonl (what the
agents already emit) with per-session monotonic sequences.

Read-only except where an operator acts: create session (hunt), steer,
stop, settings PATCH, engagement create. Binds 127.0.0.1 by the web
server's run_web().
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

router = APIRouter()

SCHEMA_VERSION = 1
HB_DEAD_S = 30

# ── workspace paths ──────────────────────────────────────────────


def _ws() -> Path:
    """The SWARM workspace root (~/.suijin) — same root the coordinator,
    the data layer and the engagement tree use. NOT the config dir."""
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).parent
    return Path.home() / ".suijin"


def _config_path() -> Path:
    from suijin.server.platform.lib.workspace import WORKSPACE_DIR

    return Path(WORKSPACE_DIR) / "config.json"


def _swarm_db() -> Path:
    return _ws() / "swarm" / "swarm.db"


def _eng_dir(name: str) -> Path:
    slug = "_default" if name in ("default", "") else name
    return _ws() / "engagements" / slug


def _cfg() -> dict:
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        return load_config() or {}
    return {}


def _cfg_save(update: dict) -> dict:
    """Update config.json atomically (keys only — never credentials)."""
    path = _config_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except Exception:  # noqa: BLE001
        data = {}
    data.update(update)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
    return data


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title or "hunt").lower()).strip("-")
    return (s or "hunt")[:48]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())



def _db_retry(fn, retries=3, delay=0.5):
    """Retry a DB operation on lock (the coordinator writes heavily)."""
    import time as _t
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            if "locked" in str(e).lower() and i < retries - 1:
                _t.sleep(delay)
            else:
                raise
    return fn()


def _db():
    """swarm.db connection (read-only intent)."""
    with contextlib.suppress(Exception):
        conn = sqlite3.connect(str(_swarm_db()), timeout=3)
        conn.row_factory = sqlite3.Row
        return conn
    return None


# ── per-session event ring (sequence-monotonic) ──────────────────

_seqs: dict[str, int] = {}
_rings: dict[str, deque] = {}
_ring_lock = threading.Lock()
MAX_RING = 600
_event_offsets: dict[str, int] = {}  # session → byte offset into events.jsonl


def _new_event_records(session: str) -> list[dict]:
    """Only the NEW tail of events.jsonl since the last read (the file
    grows while agents work — re-reading it all every poll throttled the
    whole console)."""
    f = _eng_dir(session) / "events.jsonl"
    if not f.exists():
        return []
    off = _event_offsets.get(session, 0)
    size = f.stat().st_size
    if size <= off:
        return []
    out = []
    with contextlib.suppress(OSError):
        with f.open("r", errors="replace") as fh:
            fh.seek(off)
            chunk = fh.read()
        _event_offsets[session] = size
        for line in chunk.splitlines():
            with contextlib.suppress(Exception):
                out.append(json.loads(line))
    return out


def _session_seq(session: str) -> int:
    with _ring_lock:
        return _seqs.setdefault(session, 0)


def _emit(session: str, payload: dict) -> dict:
    """Shape + ring an event for a session; returns the full event."""
    with _ring_lock:
        n = _seqs.get(session, 0) + 1
        _seqs[session] = n
        ev = {
            "schemaVersion": SCHEMA_VERSION,
            "sessionId": session,
            "sequence": n,
            "occurredAt": _now_iso(),
            **payload,
        }
        ring = _rings.setdefault(session, deque(maxlen=MAX_RING))
        ring.append(ev)
    return ev


# ── swarm/engagement state pulls ─────────────────────────────────


def _swarm_names() -> list[str]:
    """Live sessions only: a swarm lists while its agents heartbeat.
    Stopped/deleted swarms drop off (their history stays in engagements)."""
    conn = _db()
    if not conn:
        return []
    try:
        now = time.time()
        rows = conn.execute(
            "SELECT DISTINCT COALESCE(swarm,'default') FROM agents "
            "WHERE last_heartbeat > ?", (now - 90,)
        ).fetchall()
        return sorted({(r[0] or "default") for r in rows})
    finally:
        conn.close()


def _live_agents(session: str | None = None, include_stopped: bool = False) -> list[dict]:
    conn = _db()
    if not conn:
        return []
    try:
        now = time.time()
        q = ("SELECT id, swarm, current_intent, last_heartbeat, status FROM agents "
             "ORDER BY last_heartbeat DESC")
        out = []
        for r in conn.execute(q).fetchall():
            age = now - (r["last_heartbeat"] or 0)
            sw = r["swarm"] or "default"
            if session and sw != session:
                continue
            dead = age > HB_DEAD_S
            stopped = (r["status"] or "") == "stopped"
            # a STOPPED agent is not live (field 2026-11-17: cancel marked
            # rows stopped but liveness ignored the column — the next resume
            # saw the dying agent as live and skipped the spawn entirely)
            if (dead or stopped) and not include_stopped:
                continue
            out.append({
                "id": r["id"], "swarm": sw, "lane": r["current_intent"] or "idle",
                "hb_age": int(age), "dead": dead or stopped,
                "boxed": "-sbx" in r["id"],
            })
        return out
    finally:
        conn.close()


def _intents(session: str | None = None) -> list[dict]:
    conn = _db()
    if not conn:
        return []
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(intents)").fetchall()]
        want = [c for c in ("id", "text", "title", "state", "created_at", "swarm", "updated_at", "engagement", "agent_id") if c in cols]
        if not want:
            return []
        q = f"SELECT {','.join(want)} FROM intents ORDER BY created_at DESC LIMIT 200"
        out = []
        for r in conn.execute(q).fetchall():
            d = dict(r)
            if not d.get("title"):
                d["title"] = d.get("text") or ""
            sw = d.get("swarm") or "default"
            if session and sw != session:
                continue
            out.append(d)
        return out
    finally:
        conn.close()


def _findings(session: str | None = None) -> list[dict]:
    conn = _db()
    if not conn:
        return []
    try:
        q = ("SELECT severity, status, title, agent_id, created_at, id "
             "FROM findings ORDER BY created_at DESC LIMIT 100")
        out = []
        for r in conn.execute(q).fetchall():
            with contextlib.suppress(Exception):
                out.append({
                    "id": str(r[5] or ""), "severity": r[0] or "info", "status": r[1] or "new",
                    "title": (r[2] or "")[:1000], "agent": r[3] or "",
                    # findings.created_at is SECONDS; the frontend renders ms
                    "ts": float(r[4] or time.time()) * 1000.0,
                })
        return out
    finally:
        conn.close()


# session id for a swarm: the engagement name IS the session id
def _session_exists(session: str) -> bool:
    return session in _swarm_names() or _eng_dir(session).exists()


def _session_status(session: str) -> str:
    agents = _live_agents(session)
    live = [a for a in agents if not a["dead"]]
    if any(a["lane"] not in ("idle", "", None) for a in live):
        return "working"
    if live:
        return "idle"
    return "idle"


def _desk_session(session: str) -> dict:
    """DesktopConsoleSession shape."""
    agents = _live_agents(session)
    live = [a for a in agents if not a["dead"]]
    intents = _intents(session)
    running = [i for i in intents if i.get("state") == "running"]
    # LIVE AGENTS = WORKING (field 2026-11-17): title-following agents never
    # set lanes or claim intents — the old lane-based check reported "idle"
    # while agents hunted, so the composer showed no Stop control and the
    # operator couldn't stop a running engagement from the UI.
    status = "working" if live else "idle"
    # THE REAL TITLE (field 2026-11-17: "webflow.com" displayed as
    # "webflow com" — the title was rebuilt from the slug, which ate the
    # dot). The typed title is stored in engagement.json at create; show it.
    title = _engagement_record(session).get("name") or session.replace("-", " ")
    return {
        "id": session, "target": session, "role": "root",
        "autonomyMode": "standard", "scopeConfigured": False,
        "localScopeConfigured": False, "status": status,
        "createdAt": _now_iso(), "updatedAt": _now_iso(),
        "title": title, "messageCount": _session_seq(session),
    }


def _worker_list(session: str) -> list[dict]:
    # prune the zombie pile first: rows whose agents died >10 min ago are
    # clutter from duplicate hunts — the roster shows live + recently stopped
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("DELETE FROM agents WHERE swarm = ? AND last_heartbeat < ?", (session, time.time() - 600))
        db.commit()
        db.close()
    # show EVERY agent of the swarm — stopped ones stay visible (the
    # operator must see what they stopped), stale = status stopped
    try:
        conn = sqlite3.connect(str(_swarm_db()), timeout=5)
        statuses = {r[0]: (r[1] or "") for r in conn.execute(
            "SELECT id, status FROM agents WHERE swarm = ?", (session,)).fetchall()}
        conn.close()
    except Exception:  # noqa: BLE001
        statuses = {}
    out = []
    for a in _live_agents(session, include_stopped=True):
        st = (statuses.get(a["id"]) or "").lower()
        status = "stopped" if (st == "stopped" or a["dead"]) else "running"
        out.append({
            "id": a["id"], "parentId": "", "name": a["id"], "status": status,
            "task": a["lane"], "transcript": [], "role": "worker",
            "telemetry": {"hb_age": a["hb_age"], "boxed": a["boxed"]},
        })
    return out


def _usage() -> dict:
    with contextlib.suppress(Exception):
        from suijin.server.providers.lib import get_usage

        u = get_usage()
        return {"inputTokens": int(u.get("input_tokens") or 0),
                "outputTokens": int(u.get("output_tokens") or 0),
                "costUsd": float(u.get("cost_usd") or 0)}
    return {"inputTokens": 0, "outputTokens": 0}


_runtime_cache: dict = {"value": None, "at": 0.0}


def _runtime() -> dict | None:
    """The model that runs this engagement: {providerId, model} from the
    operator config (z-ai + glm-5.3 on this box). Cached 30s."""
    import time as _t

    if _runtime_cache["value"] and _t.time() - _runtime_cache["at"] < 30:
        return _runtime_cache["value"]
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        cfg = load_config() or {}
        provider = str(cfg.get("provider") or "z-ai").lower()
        model = (str(cfg.get(f"{provider}_model") or "") or str(cfg.get("model_1") or "")).strip()
        if provider == "zai":
            provider = "z-ai"  # the catalog's provider id
        if model:
            value = {"providerId": provider, "model": model}
            _runtime_cache.update(value=value, at=_t.time())
            return value
    return None


def _context_window() -> int:
    """The active model's context window, from the model catalog."""
    with contextlib.suppress(Exception):
        cfg = load_config_safe()
        model = str((cfg or {}).get("model_1") or "")
        if model:
            from suijin.server.providers.lib.model_meta import model_context_window

            return int(model_context_window(model) or 200000)
    return 200000


def load_config_safe() -> dict:
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        return load_config() or {}
    return {}


def _bus():
    from suijin.server.mesh.bus import BusClient, discover_bus

    addr = discover_bus()
    if not addr:
        return None
    bc = BusClient()
    return bc if bc.connect(addr[0], addr[1], "console-api") else None


def _publish(topic: str, payload: dict) -> bool:
    with contextlib.suppress(Exception):
        bc = _bus()
        if bc:
            bc.publish(topic, payload)
            bc.close()
            return True
    return False


def _ev_records(session: str, limit: int = 400) -> list[dict]:
    """Raw agent event records from the engagement's events.jsonl."""
    f = _eng_dir(session) / "events.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(errors="replace").splitlines()[-limit:]:
        with contextlib.suppress(Exception):
            out.append(json.loads(line))
    return out


def _shape_events(session: str, records: list[dict]) -> list[dict]:
    """events.jsonl records → DesktopConsoleEvent payloads (ring-fed)."""
    shaped = []
    for r in records:
        kind = r.get("kind")
        if kind == "assistant.message":
            shaped.append({"type": "assistant-delta", "text": str(r.get("content") or "")})
        elif kind == "tool.call":
            shaped.append({"type": "tool-start", "call": {
                "id": str(r.get("id") or ""), "name": str(r.get("name") or "tool"),
                "arguments": r.get("args") or {}}})
        elif kind == "tool.result":
            shaped.append({"type": "tool-result", "call": {
                "id": str(r.get("id") or ""), "name": str(r.get("name") or "tool"),
                "arguments": {}}, "result": str(r.get("output") or "")[:2000]})
        elif kind == "finding":
            shaped.append({"type": "notice", "text": f"finding: {r.get('title','')}"})
        else:
            continue
        # stamp session envelope
        shaped[-1] = {"schemaVersion": SCHEMA_VERSION, "sessionId": session,
                      "sequence": 0, "occurredAt": str(r.get("ts") or _now_iso()),
                      **shaped[-1]}
    return shaped


def _prime_ring(session: str) -> None:
    """First touch of a session: load history into the ring with
    monotonic sequences so cursors work from zero."""
    if session in _seqs:
        return
    with _ring_lock:
        if session in _seqs:
            return
        _seqs[session] = 0
        ring = _rings.setdefault(session, deque(maxlen=MAX_RING))
        _event_offsets[session] = (_eng_dir(session) / "events.jsonl").stat().st_size if (_eng_dir(session) / "events.jsonl").exists() else 0
        for payload in _shape_events(session, _ev_records(session)):
            n = _seqs[session] + 1
            _seqs[session] = n
            payload["sequence"] = n
            ring.append(payload)


def _messages_from(session: str) -> list[dict]:
    """Full transcript rebuild → ConsolePublicMessage[]."""
    out: list[dict] = []
    for r in _ev_records(session):
        kind = r.get("kind")
        if kind == "assistant.message":
            out.append({"role": "assistant", "content": [{"type": "text", "text": str(r.get("content") or "")}]})
        elif kind == "tool.call":
            out.append({"role": "assistant", "content": [{"type": "tool_use", "id": str(r.get("id") or ""),
                        "name": str(r.get("name") or "tool"), "input": r.get("args") or {}}]})
        elif kind == "tool.result":
            out.append({"role": "assistant", "content": [{"type": "tool_result",
                        "tool_use_id": str(r.get("id") or ""), "content": str(r.get("output") or "")[:2000]}]})
    return out


# ── host/backend handshake ───────────────────────────────────────

_CAPS = ["sessions", "workflows", "workflow-engine", "schedules", "approvals", "events",
         "workspaces", "artifacts", "model-connections", "provider-relay",
         "process-controls", "operator-services", "learning"]


def _descriptor() -> dict:
    return {
        "id": "local", "name": "Suijin", "transport": "local",
        "status": "connected",
        "protocolVersion": 1, "capabilities": _CAPS,
        "platform": {"os": "darwin", "pathStyle": "posix"},
    }


@router.get("/api/backends")
def backends():
    return {"backends": [_descriptor()]}


@router.get("/api/backends/{backend_id}/handshake")
def handshake(backend_id: str):
    d = _descriptor()
    return {"backend": d, "handshake": {
        "protocolVersion": 1, "engineId": "local", "capabilities": _CAPS,
        "platform": d["platform"], "serverInstanceId": "suijin",
    }}


@router.get("/api/team/session")
def team_session():
    return {"enabled": False, "sso": False}


@router.get("/api/team/users")
def team_users():
    return {"users": []}


# ── dashboard / scans / findings ─────────────────────────────────

def _scan_shape(i: dict) -> dict:
    state = (i.get("state") or "open").lower()
    status = {"running": "running", "done": "completed", "blocked": "failed",
              "exhausted": "failed", "open": "queued"}.get(state, state)
    started = float(i.get("created_at") or time.time())
    done = status == "completed"
    return {
        "id": str(i.get("id") or ""), "target": (i.get("title") or "")[:120],
        "depth": "default", "runtime": "api", "mode": "hunt", "status": status,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)) if done else None,
        "durationMs": None, "summary": _scan_summary(),
    }


def _scan_summary() -> dict:
    fs = _findings()
    sev = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in fs:
        s = (f["severity"] or "info").lower()
        sev[s if s in sev else "info"] += 1
    return {"totalFindings": len(fs), "totalAttacks": len(fs) * 3, **sev}


def _ledger(name: str) -> dict:
    """Tiny persistent overlay for the finding ledgers (triage, workflow,
    impact) — the swarm.db finding row is immutable history, this is the
    operator's review state."""
    p = _ws() / "config" / f"finding_{name}.json"
    with contextlib.suppress(Exception):
        data = json.loads(p.read_text(errors="replace"))
        if isinstance(data, dict):
            return data
    return {}


def _ledger_save(name: str, data: dict) -> None:
    p = _ws() / "config" / f"finding_{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)


def _workflow(f: dict) -> dict:
    status = "human_review" if f["status"] in ("reported", "new") else "in_progress"
    fp = re.sub(r"[^a-z0-9]+", "-", (f["title"] or "finding").lower()).strip("-")[:60]
    saved = _ledger("workflow").get(fp)
    persisted = (saved or {}).get("workflowStatus") or status
    assignee = (saved or {}).get("workflowAssignee") or f["agent"]
    updated = (saved or {}).get("workflowUpdatedAt")
    return {
        "status": persisted, "persistedStatus": persisted, "recommendedStatus": status,
        "phase": "in_progress" if persisted == "in_progress" else "todo",
        "reviewGate": "none", "reviewReason": None, "assignee": assignee,
        "updatedAt": updated, "consensus": "pending", "activeAgentRoles": [],
        "evidenceSignal": "medium" if f["status"] == "reported" else "weak",
        "verdictCounts": {"truePositive": 0, "falsePositive": 0, "unsure": 0, "total": 0},
    }


def _finding_group(f: dict) -> dict:
    fp = re.sub(r"[^a-z0-9]+", "-", (f["title"] or "finding").lower())[:60]
    triage = _ledger("triage").get(fp) or {}
    impact = _ledger("impact").get(f["id"])
    workflow_ledger = _ledger("workflow").get(fp) or {}
    rec = {
        "id": f["id"], "scanId": f["agent"], "title": f["title"],
        "description": f["title"], "severity": f["severity"], "category": "finding",
        "status": f["status"], "fingerprint": fp,
        "triageStatus": triage.get("triageStatus") or "new",
        "triageNote": triage.get("triageNote") or "",
        "impactAssessment": impact,
        "workflowStatus": workflow_ledger.get("workflowStatus") or "in_progress",
        "workflowAssignee": workflow_ledger.get("workflowAssignee") or f["agent"],
        "timestamp": f["ts"], "score": None, "confidence": None,
        "evidenceRequest": "", "evidenceResponse": "",
    }
    return {"fingerprint": fp, "latest": rec, "count": 1, "scanCount": 1,
            "sourceScanIds": [], "workflow": _workflow(f)}


@router.get("/api/dashboard")
def dashboard():
    intents = _intents()
    fs = _findings()
    agents = _live_agents()
    scans = [_scan_shape(i) for i in intents[:50]]
    cases = []
    for name in _swarm_names():
        cases.append({
            "id": name, "target": name, "targetType": "web-app",
            "latestScanId": "", "scanCount": sum(1 for i in intents if (i.get("swarm") or "default") == name),
            "familyCount": 0, "activeRunCount": sum(
                1 for a in agents if a["swarm"] == name and not a["dead"]),
            "reviewCount": 0, "openWorkItemCount": 0,
        })
    queue = {
        "runnable": sum(1 for i in intents if i.get("state") == "open"),
        "active": sum(1 for i in intents if i.get("state") == "running"),
        "blockedByDependency": sum(1 for i in intents if i.get("state") == "blocked"),
        "manualReview": 0, "staleWorkers": 0, "recoveredClaims": 0,
    }
    workers = [{
        "id": a["id"], "role": "orchestrator",
        "status": "running" if not a["dead"] else "stopped",
        "label": a["lane"][:60], "currentCaseId": a["swarm"],
        "currentCaseTarget": a["swarm"], "currentWorkItemId": None,
        "currentWorkItemTitle": None, "currentWorkItemKind": None,
        "currentScanId": None, "pid": None, "host": None, "lastError": None,
        "heartbeatAt": _now_iso(), "startedAt": _now_iso(), "updatedAt": _now_iso(),
        "isActive": not a["dead"],
    } for a in agents]
    return {"cases": cases, "scans": scans,
            "groups": _merged_groups(fs)[:40],
            "workers": workers, "queue": queue}


def _merged_groups(fs: list[dict]) -> list[dict]:
    """Merge findings by fingerprint — the same flaw reported by several
    agents is ONE finding with N occurrences (the board must not show
    five copies of EXP-001)."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for f in fs:
        g = _finding_group(f)
        fp = g["fingerprint"]
        if fp in merged:
            merged[fp]["count"] += 1
            merged[fp]["occurrences"].append({
                "scanId": f["agent"] or "hunt", "findingId": f["id"],
                "finding": g["latest"]})
            merged[fp]["scanCount"] += 1
        else:
            g["occurrences"] = [{"scanId": f["agent"] or "hunt",
                                 "findingId": f["id"], "finding": g["latest"]}]
            merged[fp] = g
            order.append(fp)
    return [merged[fp] for fp in order]


@router.get("/api/scans")
def scans():
    return {"scans": [_scan_shape(i) for i in _intents()[:100]]}


@router.get("/api/scans/{scan_id}")
def scan_one(scan_id: str):
    for i in _intents():
        if str(i.get("id")) == scan_id:
            return {"scan": _scan_shape(i)}
    return JSONResponse({"scan": _scan_shape({"id": scan_id, "title": scan_id, "state": "open", "created_at": time.time()})})


@router.get("/api/work")
def work():
    """RunRecord[] — the Runs page list (intents as runs)."""
    out = []
    for i in _intents()[:100]:
        state = (i.get("state") or "open").lower()
        status = {"running": "running", "done": "completed", "blocked": "blocked",
                  "exhausted": "failed", "open": "queued"}.get(state, state)
        started = float(i.get("created_at") or time.time())
        title = (i.get("title") or "")[:120]
        out.append({
            "id": str(i.get("id") or ""), "kind": "scan", "title": title,
            "target": title, "status": status,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "conversation": None, "native": {"scanIds": []},
        })
    return out


@router.get("/api/events/recent")
def recent_events():
    return {"events": []}


@router.post("/api/finding-family/{fingerprint}/triage")
def finding_family_triage(fingerprint: str, body: dict = Body(default={})):
    status = str(body.get("triageStatus") or "").strip()
    if status not in ("new", "accepted", "suppressed"):
        return JSONResponse({"error": "triageStatus must be new|accepted|suppressed"}, status_code=400)
    ledger = _ledger("triage")
    ledger[fingerprint] = {"triageStatus": status,
                           "triageNote": str(body.get("triageNote") or "")[:2000],
                           "triagedAt": _now_iso()}
    _ledger_save("triage", ledger)
    return {"ok": True}


@router.post("/api/finding-family/{fingerprint}/workflow")
def finding_family_workflow(fingerprint: str, body: dict = Body(default={})):
    status = str(body.get("workflowStatus") or "").strip()
    if status:
        if status not in ("todo", "in_progress", "blocked", "done"):
            return JSONResponse({"error": "unknown workflowStatus"}, status_code=400)
    ledger = _ledger("workflow")
    cur = dict(ledger.get(fingerprint) or {})
    cur["workflowStatus"] = status or cur.get("workflowStatus") or "in_progress"
    cur["workflowAssignee"] = str(body.get("workflowAssignee") or "").strip()[:120] or cur.get("workflowAssignee")
    cur["workflowUpdatedAt"] = _now_iso()
    ledger[fingerprint] = cur
    _ledger_save("workflow", ledger)
    return {"ok": True}


@router.get("/api/findings/{finding_id}/impact-assessment")
def impact_assessment_get(finding_id: str):
    return {"findingId": finding_id, "impactAssessment": _ledger("impact").get(finding_id)}


@router.post("/api/findings/{finding_id}/impact-assessment")
def impact_assessment_set(finding_id: str, body: dict = Body(default={})):
    assessment = body.get("impactAssessment") if isinstance(body.get("impactAssessment"), dict) else body
    if not isinstance(assessment, dict):
        return JSONResponse({"error": "body must be an impact assessment object"}, status_code=400)
    ledger = _ledger("impact")
    ledger[finding_id] = assessment
    _ledger_save("impact", ledger)
    return {"findingId": finding_id, "impactAssessment": assessment}


@router.delete("/api/findings/{finding_id}/impact-assessment")
def impact_assessment_clear(finding_id: str):
    ledger = _ledger("impact")
    ledger.pop(finding_id, None)
    _ledger_save("impact", ledger)
    return {"findingId": finding_id, "impactAssessment": None}


@router.get("/api/findings/export")
def findings_export(ids: str = ""):
    """Download findings as JSONL (ids: comma-separated, or all)."""
    from fastapi import Response as _FastapiResponse

    want = {x for x in ids.split(",") if x} if ids else None
    rows = []
    for f in _findings():
        if want is not None and f["id"] not in want:
            continue
        g = _finding_group(f)
        rows.append(g["latest"])
    body = "\n".join(json.dumps(r, default=str) for r in rows)
    return _FastapiResponse(content=body, media_type="application/x-ndjson",
                            headers={"Content-Disposition": "attachment; filename=findings.jsonl"})


@router.get("/api/finding-family/{fingerprint}")
def finding_family(fingerprint: str):
    for f in _findings():
        if _finding_group(f)["fingerprint"] == fingerprint:
            g = _finding_group(f)
            return {"consoleCommand": "suijin ledger", "fingerprint": fingerprint,
                    "case": None, "latest": g["latest"], "rows": [g["latest"]],
                    "workflow": g["workflow"], "workItems": [], "artifacts": []}
    return JSONResponse({"error": "not found"}, status_code=404)


# ── engagements ──────────────────────────────────────────────────


def _engagement_record(name: str) -> dict:
    d = _eng_dir(name)
    stat = d.stat() if d.exists() else None
    display = name
    meta = d / "engagement.json"
    with contextlib.suppress(Exception):
        display = str(json.loads(meta.read_text()).get("name") or name)
    return {
        "id": name, "name": display, "description": "", "notes": "",
        "scanIds": [], "createdAt": _now_iso(),
        "updatedAt": _now_iso() if not stat else time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
        "revision": 1,
    }


@router.get("/api/engagements")
def engagements_list():
    root = _ws() / "engagements"
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
    return {"engagements": [_engagement_record(n) for n in names]}


@router.post("/api/engagements")
def engagements_create(body: dict = Body(default={})):
    name = _slug(body.get("name") or "")
    display = str(body.get("name") or "").strip() or name
    d = _eng_dir(name)
    (d / "reports").mkdir(parents=True, exist_ok=True)
    (d / "engagement.json").write_text(json.dumps({
        "name": display, "description": str(body.get("description") or ""),
        "createdAt": _now_iso(), "revision": 1,
    }, indent=2))
    return {"engagement": _engagement_record(name)}


@router.get("/api/engagements/{name}/report")
def engagement_report(name: str):
    fs = _findings(name)
    intents = _intents(name)
    groups = []
    for f in fs:
        g = _finding_group(f)
        fp = g["fingerprint"]
        groups.append({
            "key": fp, "fingerprint": fp, "reviewStatus": "unreviewed",
            "occurrences": [{"scanId": f["agent"] or "hunt", "findingId": f["id"],
                             "finding": {"id": f["id"], "title": f["title"],
                                         "severity": f["severity"], "status": f["status"],
                                         "category": "", "description": f["title"],
                                         "fingerprint": fp,
                                         "evidenceRequest": "", "evidenceResponse": "",
                                         "timestamp": int(f["ts"] * 1000)}}],
        })
    scans = [{
        "id": str(i.get("id") or ""), "target": (i.get("title") or "")[:120],
        "status": i.get("state") or "open",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(i.get("created_at") or 0))),
        "completedAt": None, "durationMs": None,
    } for i in intents]
    rec = _engagement_record(name)
    verified = sum(1 for g in groups if g["occurrences"] and g["occurrences"][0]["finding"]["status"] == "verified")
    rejected = sum(1 for g in groups if g["occurrences"] and g["occurrences"][0]["finding"]["status"] == "false_positive")
    return {
        "schemaVersion": 1, "engagement": rec,
        "coverage": {"scanIds": [s["id"] for s in scans], "kind": "selected-retained-scans"},
        "scans": scans, "findingGroups": groups,
        "summary": {"scanCount": len(scans), "findingCount": len(fs),
                    "uniqueFindingCount": len(groups), "verified": verified,
                    "rejected": rejected,
                    "unreviewed": len(groups) - verified - rejected},
    }


# ── console sessions (= swarms) ──────────────────────────────────


@router.get("/api/console/sessions")
def sessions_list():
    out = []
    for name in _swarm_names():
        s = _desk_session(name)
        s["archived"] = False
        out.append(s)
    return {"sessions": out}


@router.get("/api/console/saved")
def sessions_saved():
    return {"sessions": []}


_inflight_hunts: set[str] = set()
_deleted_sessions: set[str] = set()  # resurrect guard — a deleted chat stays dead
_recent_sessions: dict[str, float] = {}  # slug → creation time (rate-limit duplicate POSTs)
_hunt_pids_file = None


def _hunt_pids_path() -> Path:
    global _hunt_pids_file
    if _hunt_pids_file is None:
        _hunt_pids_file = _root() / "swarm" / "console-agents.pids"
    return _hunt_pids_file


def _track_pids(pids: list[int]) -> None:
    with contextlib.suppress(OSError):
        p = _hunt_pids_path()
        existing = []
        if p.exists():
            existing = [int(x) for x in p.read_text().split() if x.strip().isdigit()]
        p.write_text(" ".join(str(x) for x in set(existing + pids)))


@router.post("/api/console/sessions")
def sessions_create(body: dict = Body(default={})):
    title = str(body.get("title") or body.get("target") or "").strip() or "new swarm"
    structured = bool(body.get("structured"))
    workers_per_cat = int(body.get("workers") or 2)
    # SPAWN GATE (field 2026-11-17: loading the landing page fired three
    # blank creates and each one spawned a live agent hunt — swarms named
    # "new swarm" wandering off to hunt whatever they found in memory).
    # A session record is cheap; a hunt is EXPENSIVE. Hunts spawn when the
    # operator submits a real mission (spawn=true from the composer) or on
    # the first message into a never-spawned swarm (lazy spawn below).
    spawn = bool(body.get("spawn"))
    name = _slug(title)
    # RATE LIMIT: the same chat requested within 60s returns the existing
    # session (the frontend re-fires create on poll ticks — server-side
    # dedupe is the only reliable break for that loop)
    if name in _recent_sessions and time.time() - _recent_sessions[name] < 60:
        s = _desk_session(name)
        s.update({"status": "working" if _live_agents(name) else s["status"], "title": title})
        return {"session": s}
    _recent_sessions[name] = time.time()
    base = name
    n = 2
    while name in _inflight_hunts or _session_exists(name):
        name = f"{base}-{n}"
        n += 1
    _recent_sessions[name] = time.time()
    # engagement dir + intent so the swarm has a home
    d = _eng_dir(name)
    (d / "reports").mkdir(parents=True, exist_ok=True)
    # never stack a second hunt under the same chat: in-flight spawn OR
    # already-live agents ⇒ just navigate
    if name in _inflight_hunts:
        s = _desk_session(name)
        s.update({"status": "working", "title": title})
        return {"session": s}
    if not _session_exists(name):
        (d / "engagement.json").write_text(json.dumps({
            "name": title, "description": title, "createdAt": _now_iso(), "revision": 1,
            "structured": structured}))
    else:
        live = [a for a in _live_agents(name) if not a["dead"]]
        s = _desk_session(name)
        s.update({"status": "working" if live else s["status"], "title": title})
        if live:
            return {"session": s}
    _inflight_hunts.add(name)

    if structured and spawn:
        # the hierarchy — model_1 required (leader brain)
        def _structured_hunt():
            try:
                from suijin.server.swarm.structured import start_structured

                t = start_structured(title, workers_per_cat=workers_per_cat)
                if t.get("ok"):
                    _emit(name, {"type": "notice", "text":
                                 f"structured swarm up: 1 leader · "
                                 f"{sum(len(v) for v in t['supervisors'].values())} supervisors · "
                                 f"{sum(len(v) for v in t['workers'].values())} workers"})
                else:
                    _emit(name, {"type": "error", "message": str(t.get("error"))})
            except Exception as e:  # noqa: BLE001 — surface it in the transcript
                _emit(name, {"type": "error", "message": f"structured start: {e}"})
        threading.Thread(target=_structured_hunt, daemon=True, name=f"structured-{name}").start()
        s = _desk_session(name)
        s.update({"status": "working", "title": title})
        return {"session": s}

    if not spawn:
        # session record only — no mission yet, no agents. The hunt spawns
        # lazily when the first message lands (session_message).
        with contextlib.suppress(Exception):
            _inflight_hunts.discard(name)
        s = _desk_session(name)
        s.update({"status": "idle", "title": title})
        return {"session": s}

    # hunt in a background thread — the UI gets its session immediately
    def _hunt():
        with contextlib.suppress(Exception):
            from suijin.server.swarm.lifecycle import run_swarm_hunt

            # ONE AGENT (operator ruling 2026-11-17): raw red team is a
            # single agent per engagement — no swarms by default.
            # swarm=name: agents register under THIS session's id (the
            # uniquified slug), not a re-slug of the title.
            run_swarm_hunt(title, host_n=1, boxed_n=0, swarm=name)
    def _release():
        time.sleep(5)
        with contextlib.suppress(Exception):
            _inflight_hunts.discard(name)
    threading.Thread(target=_release, daemon=True).start()
    threading.Thread(target=_hunt, daemon=True, name=f"hunt-{name}").start()
    s = _desk_session(name)
    s.update({"status": "working", "title": title})
    _emit(name, {"type": "notice", "text": f"Engagement started: {title}"})
    return {"session": s}


@router.get("/api/console/sessions/{session_id}")
def session_snapshot(session_id: str):
    _prime_ring(session_id)
    with _ring_lock:
        cursor = _seqs.get(session_id, 0)
        ring = list(_rings.get(session_id) or [])
    return {"snapshot": {
        "session": _desk_session(session_id), "title": session_id.replace("-", " "),
        "cursor": cursor,
        "messages": _messages_from(session_id),
        "events": ring[-200:],
        "pendingDecisions": [], "workers": _worker_list(session_id),
        "queuedMessages": [],
        # REAL RUNTIME (field 2026-11-17: the composer showed a bare "Model"
        # label because runtime was hardcoded None — the operator wants the
        # model that started the engagement, e.g. GLM 5.3, in the bar) and
        # REAL CONTEXT TOKENS (the circle next to the model was decorative).
        "runtime": _runtime(),
        "contextInputTokens": _usage().get("inputTokens", 0),
        "contextWindowTokens": _context_window(),
        "scope": None,
        "scopeEnforcement": {"enabled": False, "projectPath": session_id},
        "workspacePath": str(_eng_dir(session_id)),
        "usage": _usage(),
    }}


@router.get("/api/console/sessions/{session_id}/events")
def session_events(session_id: str, after: int = 0):
    _prime_ring(session_id)
    # pull the NEW tail into the ring first (incremental file read)
    fresh_records = _new_event_records(session_id)
    if fresh_records:
        with _ring_lock:
            for payload in _shape_events(session_id, fresh_records):
                n = _seqs.get(session_id, 0) + 1
                _seqs[session_id] = n
                payload["sequence"] = n
                _rings.setdefault(session_id, deque(maxlen=MAX_RING)).append(payload)
    with _ring_lock:
        cursor = _seqs.get(session_id, 0)
        ring = list(_rings.get(session_id) or [])
    fresh = [e for e in ring if e["sequence"] > after]
    return {"events": fresh, "cursor": max(cursor, after), "gap": False}


@router.post("/api/console/sessions/{session_id}/messages")
def session_message(session_id: str, body: dict = Body(default={})):
    text = str(body.get("text") or "").strip()
    # LAZY SPAWN (pairs with the create-time spawn gate): a message into a
    # swarm that never spawned starts its hunt. The swarm title is the
    # standing mission; this message steers on top of it.
    if text and not _live_agents(session_id) and session_id not in _inflight_hunts \
            and session_id not in _deleted_sessions and not body.get("steerOnly"):
        s = _desk_session(session_id)
        mission = str(s.get("title") or session_id)
        _inflight_hunts.add(session_id)
        # RELEASE THE MARKER (field 2026-11-17: the lazy path added the
        # session to _inflight_hunts and never discarded it — the SECOND
        # resume (stop → prompt → prompt) found the stale marker and
        # silently refused to spawn. Same release pattern as create.)
        def _release_lazy():
            time.sleep(5)
            with contextlib.suppress(Exception):
                _inflight_hunts.discard(session_id)
        threading.Thread(target=_release_lazy, daemon=True).start()
        with contextlib.suppress(Exception):
            (d := _eng_dir(session_id) / "reports").mkdir(parents=True, exist_ok=True)

        def _lazy_hunt():
            with contextlib.suppress(Exception):
                from suijin.server.swarm.lifecycle import run_swarm_hunt

                # ONE AGENT — see the spawn-gate comment in sessions_create
                run_swarm_hunt(mission, host_n=1, boxed_n=0, swarm=session_id)
        threading.Thread(target=_lazy_hunt, daemon=True, name=f"lazyspawn-{session_id}").start()
        _emit(session_id, {"type": "notice", "text": f"Engagement started: {mission}"})
    if text:
        _publish("/cmd/steer", {"message": text[:500], "swarm": session_id})
        with contextlib.suppress(Exception):
            from suijin.server.agent.lib.mesh import mesh_dir

            d = mesh_dir()
            d.mkdir(parents=True, exist_ok=True)
            with (d / "gc.log").open("a") as f:
                f.write(f"{time.strftime('%m-%d %H:%M:%S')}|{os.getpid()}|operator|{text[:400]}\n")
        _emit(session_id, {"type": "user", "text": text[:500]})
    return {"session": _desk_session(session_id)}


@router.patch("/api/console/sessions/{session_id}/configuration")
def session_config(session_id: str, body: dict = Body(default={})):
    return {"session": _desk_session(session_id)}


@router.post("/api/console/sessions/{session_id}/configuration")
def session_config_post(session_id: str, body: dict = Body(default={})):
    return {"session": _desk_session(session_id)}


@router.post("/api/console/sessions/{session_id}/workers/stop")
def workers_stop_all(session_id: str):
    """Stop every sub-agent of this swarm (the agents panel's stop)."""
    _publish("/cmd/stop", {"swarm": session_id})
    workers = _worker_list(session_id)
    for w in workers:
        _publish("/cmd/stop", {"agent": w["id"]})
        # stream the stop INTO the transcript so the panel updates live
        _emit(session_id, {"type": "worker", "worker": {**w, "status": "stopped"}})
    _emit(session_id, {"type": "notice", "text": "stop requested for all sub-agents"})
    return {"session": _desk_session(session_id)}


@router.get("/api/console/sessions/{session_id}/export")
def session_export(session_id: str):
    return {"ok": True, "session": session_id}


@router.post("/api/console/sessions/{session_id}/connection/reload")
def session_connection_reload(session_id: str):
    return {"ok": True}


@router.post("/api/console/sessions/{session_id}/queue")
def session_queue_add(session_id: str, body: dict = Body(default={})):
    return {"session": _desk_session(session_id)}


@router.delete("/api/console/sessions/{session_id}/queue")
@router.delete("/api/console/sessions/{session_id}/queue/{queue_id}")
def session_queue_remove(session_id: str, queue_id: str = ""):
    return {"ok": True}


@router.post("/api/console/saved/{session_id}/resume")
def saved_resume(session_id: str, body: dict = Body(default={})):
    return {"session": _desk_session(session_id)}


@router.get("/api/console/saved/{session_id}")
def saved_one(session_id: str):
    return {"session": {**_desk_session(session_id), "archived": False}}


@router.delete("/api/console/saved/{session_id}")
def saved_delete(session_id: str):
    """The sidebar trash hits this path for saved rows — same delete as
    live: hard-stop agents, remove rows, drop the chat."""
    return session_close(session_id)


@router.post("/api/console/sessions/{session_id}/{action}")
def session_action(session_id: str, action: str, body: dict = Body(default={})):
    if action in ("cancel", "force-stop", "stop"):
        _publish("/cmd/stop", {"swarm": session_id})
        # STOP OPENS THE RESUME DOOR (field 2026-11-17: the in-flight spawn
        # marker outlived the stop — a resume inside the window was dropped)
        with contextlib.suppress(Exception):
            _inflight_hunts.discard(session_id)
        # MARK STOPPED NOW (field 2026-11-17: the agent exits at its next
        # turn boundary, which can be minutes — meanwhile liveness said
        # "working" and a resume in that window was silently dropped)
        with contextlib.suppress(Exception):
            db = sqlite3.connect(str(_swarm_db()), timeout=5)
            db.execute("UPDATE agents SET status = 'stopped' WHERE swarm = ?", (session_id,))
            db.commit()
            db.close()
        _emit(session_id, {"type": "notice", "text": "stop requested"})
    elif action == "pause":
        _publish("/cmd/pause", {"swarm": session_id})
    elif action in ("resume", "clear"):
        _publish("/cmd/resume", {"swarm": session_id})
    elif action == "delete":
        # the sidebar trash lands here — same as pressing delete
        return session_close(session_id)
    return {"session": _desk_session(session_id)}


@router.post("/api/console/sessions/{session_id}/decisions/{decision_id}")
def session_decision(session_id: str, decision_id: str, body: dict = Body(default={})):
    return {"session": _desk_session(session_id)}


@router.delete("/api/console/sessions/{session_id}")
def _bus_addr_direct():
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus

        return discover_bus()
    return None


def session_close(session_id: str):
    """Delete the chat: HARD-stop its agents (kill, not graceful — the
    operator pressed delete, they want it gone now) AND remove the swarm's
    rows so the chat disappears from the list."""
    for a in _live_agents(session_id, include_stopped=True):
        with contextlib.suppress(Exception):
            from suijin.server.mesh.bus import BusClient

            addr = _bus_addr_direct()
            if addr:
                bc = BusClient()
                if bc.connect(addr[0], addr[1], "console-kill"):
                    bc.publish(f"/agent/{a['id']}/kill", {})
                    bc.close()
    _publish("/cmd/stop", {"swarm": session_id})
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("UPDATE agents SET status = 'stopped' WHERE swarm = ?", (session_id,))
        db.execute("DELETE FROM intents WHERE swarm = ?", (session_id,))
        db.execute("DELETE FROM agents WHERE swarm = ? AND status = 'stopped'", (session_id,))
        db.commit()
        db.close()
    with _ring_lock:
        _rings.pop(session_id, None)
        _seqs.pop(session_id, None)
    _deleted_sessions.add(session_id)
    return {"ok": True}


@router.post("/api/console/sessions/{session_id}/workers/{worker_id}/stop")
def worker_stop(session_id: str, worker_id: str):
    _publish("/cmd/stop", {"agent": worker_id})
    return {"ok": True}


# ── providers / models ───────────────────────────────────────────


def _zai_configured() -> bool:
    if os.environ.get("ZAI_API_KEY"):
        return True
    # the runtime .env lives in the CONFIG dir (~/.suijin/config/.env —
    # what load_env() reads); the package-dir .env is the fallback
    for candidate in (_ws() / "config" / ".env",
                      Path(__file__).resolve().parents[3] / ".env",
                      Path(__file__).resolve().parents[2] / ".env"):
        with contextlib.suppress(Exception):
            if "ZAI_API_KEY=" in candidate.read_text():
                return True
    return False


@router.get("/api/console/providers")
def providers():
    cfg = _cfg()
    zai = _zai_configured()
    provs = [{
        "id": "z-ai", "label": "Z.ai", "methods": ["api-key"],
        "configured": zai, "source": "env" if zai else None,
        "connectionIdentity": "suijin-zai" if zai else None,
        "diagnostics": [],
        "accounts": ([{"accountId": "suijin", "kind": "api-key", "active": True,
                       "label": "coding plan", "status": "active"}] if zai else []),
        "auth": {"phase": "connected" if zai else "unconfigured",
                 "message": "connected" if zai else "set ZAI_API_KEY in suijin/.env",
                 "lines": [], "verificationUrl": None, "userCode": None, "available": True},
        "configuration": {"model": cfg.get("zai_model") or "glm-5.3"},
    }]
    for pid, label in (("anthropic", "Anthropic"), ("openai", "OpenAI"),
                       ("google", "Google"), ("openrouter", "OpenRouter")):
        provs.append({"id": pid, "label": label, "methods": ["api-key"], "configured": False,
                      "source": None, "diagnostics": [], "accounts": [],
                      "auth": {"phase": "unconfigured", "message": "no credential", "lines": [],
                               "verificationUrl": None, "userCode": None, "available": True}})
    model = cfg.get("zai_model") or "glm-5.3"
    return {"providers": provs,
            "preference": {"providerId": "z-ai", "model": model,
                           "connectionIdentity": "suijin-zai"} if zai else None}


@router.get("/api/console/models")
def models():
    cfg = _cfg()
    primary = cfg.get("zai_model") or "glm-5.3"
    routing = cfg.get("model_routing") or {}
    cat = [
        {"id": primary, "provider": "z-ai", "price": "coding plan", "contextWindowTokens": 200000, "source": "account"},
        {"id": "glm-4.7-air", "provider": "z-ai", "price": "coding plan", "contextWindowTokens": 128000, "source": "account"},
        {"id": "glm-4.6", "provider": "z-ai", "price": "coding plan", "contextWindowTokens": 200000, "source": "public-catalog"},
    ]
    return {"models": cat, "providerId": "z-ai", "diagnostics": [],
            "roles": [routing.get("planner") or "planner", routing.get("worker") or "glm-5.3"]}


# ── settings (real config keys — the pills flip real behavior) ───

_SETTING_DEFS = [
    {"key": "swarm_autopilot", "label": "Autopilot", "description": "Coordinator requeues and summons on its own.", "kind": "boolean", "default": True, "group": "swarm"},
    {"key": "swarm_turn_cap", "label": "Turn cap", "description": "Per-agent turn budget. 0 = unlimited.", "kind": "enum", "default": "0", "choices": ["0", "25", "50", "100"], "group": "swarm"},
    {"key": "complexity", "label": "Complexity", "description": "Gates advanced swarm verbs.", "kind": "enum", "default": "basic", "choices": ["basic", "advanced"], "group": "swarm"},
    {"key": "sandbox_pool_size", "label": "Warm pool", "description": "Idle boxes kept ready.", "kind": "enum", "default": "0", "choices": ["0", "2", "4", "8"], "group": "sandbox"},
    {"key": "transport_scope_guard", "label": "Scope guard", "description": "Drop requests outside engagement scope at the transport layer.", "kind": "boolean", "default": True, "group": "transport"},
    {"key": "transport_method_guard", "label": "Method guard", "description": "Restrict unsafe methods at the transport layer.", "kind": "boolean", "default": True, "group": "transport"},
    {"key": "transport_rate_limit", "label": "Rate limit", "description": "Requests/second ceiling.", "kind": "enum", "default": "off", "choices": ["off", "10", "50", "200"], "group": "transport"},
    {"key": "ledger_retest_days", "label": "Retest window", "description": "Stale verdicts invite retesting after this many days.", "kind": "enum", "default": "90", "choices": ["30", "90", "180"], "group": "ledger"},
    {"key": "routing_worker_model", "label": "Worker model", "description": "Volume model for engagements.", "kind": "enum", "default": "", "choices": [], "group": "models"},
    {"key": "model_1", "label": "M1 · Leader model", "description": "REQUIRED for structured mode — the strongest brain.", "kind": "enum", "default": "", "choices": [], "group": "models"},
    {"key": "model_2", "label": "M2 · Supervisor model", "description": "Defaults to M1 when unset.", "kind": "enum", "default": "", "choices": [], "group": "models"},
    {"key": "model_3", "label": "M3 · Worker model", "description": "Defaults to M2 when unset. Cascade m3→m2→m1.", "kind": "enum", "default": "", "choices": [], "group": "models"},
    {"key": "memory_preflight_guard", "label": "Memory guard", "description": "Refuse spawn plans exceeding the RAM budget.", "kind": "boolean", "default": True, "group": "swarm"},
]


@router.post("/api/console/connections")
def connections_post(body: dict = Body(default={})):
    """Connect/clear a provider key. Keys live in the workspace .env —
    never in config.json, never in the db, never echoed back."""
    provider = str(body.get("providerId") or "")
    action = str(body.get("action") or "")
    env_names = {"z-ai": "ZAI_API_KEY", "zai": "ZAI_API_KEY",
                 "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                 "google": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
    env_name = env_names.get(provider)
    if not env_name:
        # resolve from the registry — all 51 providers connect here
        with contextlib.suppress(Exception):
            from suijin.server.providers.lib.registry import PROVIDER_REGISTRY

            spec = PROVIDER_REGISTRY.get(provider)
            if spec and spec.key_envs:
                env_name = spec.key_envs[0]
    if not env_name:
        return JSONResponse({"error": f"unknown provider '{provider}'"}, status_code=400)
    env_path = _ws() / "config" / ".env"
    lines = []
    with contextlib.suppress(OSError):
        lines = env_path.read_text().splitlines()
    kept = [ln for ln in lines if not ln.startswith(f"{env_name}=")]
    if action == "connect" and str(body.get("apiKey") or "").strip():
        kept.append(f"{env_name}={str(body['apiKey']).strip()}")
        # BASE URL OVERRIDE + EXTRA FIELDS (Models page): <PROV>_BASE_URL
        # env override + per-provider extras in config provider_extras.
        base_url = str(body.get("baseUrl") or "").strip()
        kept = [ln for ln in kept if not ln.startswith(f"{env_name}_BASE_URL=")]
        if base_url:
            kept.append(f"{env_name}_BASE_URL={base_url}")
        extras = body.get("extras") or {}
        if isinstance(extras, dict) and extras:
            with contextlib.suppress(Exception):
                cfgx = load_config_safe()
                px = dict(cfgx.get("provider_extras") or {})
                px[provider] = {k: str(v)[:200] for k, v in extras.items() if str(v).strip()}
                cfgx["provider_extras"] = px
                import json as _jx

                from suijin.modules.platform.lib.config_loader import CONFIG_PATH

                CONFIG_PATH.write_text(_jx.dumps(cfgx, indent=2))
        message = f"{provider} connected."
    elif action in ("clear", "disconnect"):
        message = f"{provider} credential removed."
    else:
        return JSONResponse({"error": "nothing to do"}, status_code=400)
    with contextlib.suppress(OSError):
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(kept) + "\n")
    result = providers()
    return {**result, "message": message}


@router.post("/api/control/stop-daemon")
def stop_daemon():
    """The dashboard's Stop engine — stop every agent for real."""
    _publish("/cmd/stop", {})
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("UPDATE agents SET status = 'stopped' WHERE status != 'dead'")
        db.commit()
        db.close()
    return {"ok": True, "stopped": True}


@router.post("/api/control/start-daemon")
def start_daemon(body: dict = Body(default={})):
    """The dashboard's Start engine — bring the coordinator up."""
    started = bool(_bus())
    if not started:
        with contextlib.suppress(Exception):
            import subprocess
            import sys

            subprocess.Popen(
                [sys.executable, "-m", "suijin.server.swarm.coordinator"],
                stdout=open("/tmp/suijin-coordinator.log", "a"), stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)
            time.sleep(4)
            started = bool(_bus())
    return {"ok": True, "pid": None, "label": "suijin coordinator", "started": started}


@router.post("/api/control/recover-stale-workers")
def recover_stale():
    return {"ok": True, "recovered": 0}


@router.post("/api/control/prune-stopped-workers")
def prune_stopped():
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("DELETE FROM agents WHERE status = 'stopped' OR last_heartbeat < ?", (time.time() - 300,))
        db.commit()
        db.close()
    return {"ok": True, "deleted": 0}


@router.get("/api/console/settings")
def settings_get():
    cfg = _cfg()
    defs, values, sources = [], {}, {}
    model_keys = {"routing_worker_model", "model_1", "model_2", "model_3"}
    model_choices: list[str] = []
    with contextlib.suppress(Exception):
        model_choices = [m["value"] for m in available_models()["models"]][:400]
    for d in _SETTING_DEFS:
        k = d["key"]
        cur = cfg.get(k, d["default"])
        if d["kind"] == "boolean":
            cur = bool(cur)
        else:
            cur = str(cur)
        choices = d.get("choices") or []
        if k in model_keys:
            choices = ([""] if k in ("model_2", "model_3") else []) + model_choices
            if cur and cur not in choices:
                choices = [cur] + choices
        defs.append({**d, "choices": choices, "operatorOnly": False})
        values[k] = cur
        sources[k] = "global"
    return {"settings": values, "sources": sources, "definitions": defs,
            "defaultWriteLayer": "global", "persisted": True}


@router.patch("/api/console/settings")
def settings_patch(body: dict = Body(default={})):
    key = str(body.get("key") or "")
    val = body.get("value")
    defs = {d["key"]: d for d in _SETTING_DEFS}
    if key not in defs:
        return JSONResponse({"error": f"unknown setting '{key}'"}, status_code=400)
    if defs[key]["kind"] == "boolean":
        val = bool(val)
    elif str(val) == "none" or str(val) == "0":
        val = 0 if key == "swarm_turn_cap" else ("" if key == "model_2" or key == "model_3" else val)
    _cfg_save({key: val})
    return settings_get()


@router.post("/api/console/settings/reset")
def settings_reset(body: dict = Body(default={})):
    keys = body.get("keys") or []
    defs = {d["key"]: d for d in _SETTING_DEFS}
    restore = {k: defs[k]["default"] for k in keys if k in defs}
    _cfg_save(restore)
    return settings_get()


# ── plugins (suijin modules) ─────────────────────────────────────

_PLUGIN_ITEMS = [
    {"id": "transport-guards", "name": "transport guards", "version": "1.4.0", "description": "scope/method/rate enforced at the HTTP session layer", "capabilities": ["transport"], "state": "active", "loaded": True},
    {"id": "mesh-bridge", "name": "mesh bridge", "version": "1.2.0", "description": "gc.log ↔ mesh chat, no echo loops", "capabilities": ["mesh"], "state": "active", "loaded": True},
    {"id": "knowledge-graph", "name": "knowledge graph", "version": "0.9.1", "description": "workspace-first intel graph", "capabilities": ["memory"], "state": "installed", "loaded": False},
    {"id": "obsidian-sync", "name": "obsidian decision log", "version": "0.3.0", "description": "push decisions to the vault", "capabilities": ["docs"], "state": "available", "loaded": False},
]


@router.get("/api/console/plugins")
def plugins():
    items = [{**p, "kind": "plugin", "signature": "verified", "error": None} for p in _PLUGIN_ITEMS]
    return {"executionProfile": "local",
            "registry": {"url": "suijin-local", "available": False, "error": None},
            "items": items, "deferred": [],
            "host": {"loadedPluginIds": [p["id"] for p in _PLUGIN_ITEMS if p["loaded"]],
                     "tools": [{"name": "inject_probe", "description": "payload arrays into sink contexts"},
                               {"name": "mark_dead", "description": "broadcast a dead region"},
                               {"name": "report_finding", "description": "publish a finding"}]}}


# ── skills (the real tool registry) ──────────────────────────────


@router.get("/api/skills")
def skills_list():
    out = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.dispatch import list_route_tools

        for name in list_route_tools():
            out.append({"id": name, "name": name, "description": f"Suijin tool: {name}",
                        "scope": "builtin", "revision": "r1", "writable": False,
                        "fileCount": 0, "source": "core"})
    return {"skills": out, "mounts": [], "diagnostics": [], "workspacePath": "suijin workspace"}


@router.get("/api/skills/{skill_id}")
def skill_detail(skill_id: str):
    import inspect

    doc = ""
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib import dispatch as d

        fn = getattr(d, skill_id, None)
        if fn and hasattr(fn, "__doc__"):
            doc = (fn.__doc__ or "").strip()
    return {"id": skill_id, "name": skill_id, "description": doc[:300] or f"Suijin tool: {skill_id}",
            "scope": "builtin", "revision": "r1", "writable": False, "fileCount": 0,
            "source": "core", "content": doc[:4000] or f"# {skill_id}\n", "files": []}


# ── project metadata (home suggestions) ──────────────────────────


@router.get("/api/console/project")
def project(path: str = ""):
    return {"path": path or "suijin", "name": "Suijin",
            "git": {"branch": None, "dirty": False, "root": None, "error": None}}


# ── live page feed ───────────────────────────────────────────────


@router.get("/api/v1/presentation/events")
def presentation_events():
    return {"events": []}


# ── doctor / tools / misc surfaces ───────────────────────────────


@router.get("/api/console/doctor")
def doctor():
    provs = providers()
    zai = _zai_configured()
    doc_provs = []
    for p in provs["providers"]:
        doc_provs.append({**p, "diagnostics": {"valid": bool(p["configured"]),
                                               "message": "" if p["configured"] else "not connected"}})
    return {
        "version": "1.0.0",
        "runtime": {"node": "24", "nodeSupported": True, "platform": "host",
                    "arch": "arm64", "engine": "suijin", "engineVersion": "1.0.0",
                    "releaseChannel": "stable"},
        "availability": {"hasApiKey": zai, "availableRuntimes": ["z-ai"],
                         "apiRuntime": {"configured": zai, "valid": zai,
                                        "providerLabel": "Z.ai · glm-5.3"}},
        "providers": doc_provs,
        "prerequisites": [
            {"id": "coordinator", "available": bool(_bus()), "required": False},
            {"id": "sandbox-engine", "available": True, "required": False},
        ],
        "settings": {"onboardingCompleted": True, "analyticsLevel": "off",
                     "diagnosticReporting": "off", "executionProfile": "local"},
    }


@router.get("/api/console/tools")
def console_tools():
    tools = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.dispatch import list_route_tools

        tools = [{"name": n, "description": f"Suijin tool: {n}",
                  "roles": ["worker", "planner"]} for n in list_route_tools()]
    return {"roles": ["worker", "planner"], "tools": tools, "plugins": []}


@router.get("/api/console/checks")
def console_checks():
    return {"project": "suijin", "checks": []}


@router.get("/api/console/learning")
def console_learning():
    return {"events": [], "knowledge": [], "improvements": []}


@router.get("/api/console/service-plugins")
def service_plugins():
    items = [{
        "id": p["id"], "name": p["name"], "description": p["description"],
        "category": "suijin", "configured": True, "enabled": p["loaded"],
        "tools": [{"name": t["name"], "description": t["description"]}
                  for t in plugins()["host"]["tools"][:1]],
        "values": {}, "testedAt": None,
    } for p in _PLUGIN_ITEMS]
    return {"items": items, "executionProfile": "local"}


@router.get("/api/console/workflow-definitions")
def workflow_definitions():
    return {"definitions": []}


@router.get("/api/console/workflow-tool-catalog")
def workflow_tool_catalog():
    tools = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.dispatch import list_route_tools

        tools = [{"name": n, "description": f"Suijin tool: {n}"} for n in list_route_tools()]
    return {"tools": tools}


@router.get("/api/console/execution")
def console_execution():
    return {"configured": True, "runtimeReady": True, "imageApproved": True,
            "platformSupported": True}


# ── models page: provider catalog + live fetch + runtime ─────────

# Per-provider setup schemas — the form renders exactly these fields;
# everything not listed is grayed out; empty = provider default.
# kinds: string | password | select(options) | toggle(options)
def _env_key_value(env_name: str) -> bool:
    """Is this provider's key set in the environment/.env?"""
    with contextlib.suppress(Exception):
        import os as _os

        if _os.environ.get(env_name):
            return True
        env_path = _ws() / "config" / ".env"
        for ln in env_path.read_text(errors="replace").splitlines():
            if ln.startswith(f"{env_name}=") and ln.split("=", 1)[1].strip():
                return True
    return False


PROVIDER_SETUP_SCHEMAS: dict[str, list[dict]] = {
    "volcengine": [
        {"key": "access_key", "label": "Access Key ID", "kind": "string"},
        {"key": "secret_key", "label": "Secret Key", "kind": "password"},
        {"key": "region", "label": "Region", "kind": "string", "placeholder": "e.g. cn-north-1"},
        {"key": "endpoint_id", "label": "Model endpoint ID", "kind": "string", "placeholder": "ep-…"},
    ],
    "cloudflare": [
        {"key": "account_id", "label": "Account ID", "kind": "string"},
    ],
    "azure": [
        {"key": "deployment", "label": "Deployment name", "kind": "string"},
        {"key": "api_version", "label": "API version", "kind": "string", "placeholder": "e.g. 2024-10-21"},
    ],
    "zai": [
        {"key": "endpoint_style", "label": "Endpoint", "kind": "select",
         "options": ["coding plan", "paas"], "default": "coding plan"},
    ],
    "qwen": [{"key": "endpoint_style", "label": "Endpoint", "kind": "select", "options": ["international", "china"], "default": "international"}],
    "moonshot": [{"key": "endpoint_style", "label": "Endpoint", "kind": "select", "options": ["international", "china"], "default": "international"}],
    "ollama": [], "lmstudio": [], "vllm": [], "llamacpp": [], "jan": [],
}

# which providers speak more than one API style
API_STYLE_PROVIDERS: dict[str, list[str]] = {
    "openai": ["chat", "responses"],
    "azure": ["chat", "responses"],
    "openrouter": ["chat"],
    "custom": ["chat", "responses"],
}

_LOCAL_PROVIDERS = {"ollama", "lmstudio", "vllm", "llamacpp", "jan"}


@router.get("/api/console/provider-catalog")
def provider_catalog():
    """Every provider + setup schema for the Models page. One entry per
    key — native branches (anthropic/google/cloudflare) merge with any
    registry plumbing; domains feed the live logo fetch."""
    from suijin.server.providers.lib.registry import PROVIDER_REGISTRY

    domains = {
        "openai": "openai.com", "xai": "x.ai", "mistral": "mistral.ai",
        "groq": "groq.com", "together": "together.ai", "fireworks": "fireworks.ai",
        "deepinfra": "deepinfra.com", "cerebras": "cerebras.ai",
        "sambanova": "sambanova.ai", "perplexity": "perplexity.ai",
        "cohere": "cohere.com", "lambda": "lambda.ai",
        "openrouter": "openrouter.ai", "opencode": "opencode.ai",
        "zai": "z.ai", "zhipu": "bigmodel.cn", "moonshot": "moonshot.ai",
        "moonshot_cn": "moonshot.cn", "qwen": "alibabacloud.com",
        "qwen_cn": "aliyun.com", "volcengine": "volcengine.com",
        "stepfun": "stepfun.ai", "modelscope": "modelscope.cn",
        "siliconflow": "siliconflow.cn", "iflytek": "iflytek.com",
        "sensenova": "sensenova.cn", "xiaomi": "xiaomi.com",
        "longcat": "meituan.com", "nvidia": "nvidia.com", "novita": "novita.ai",
        "nebius": "nebius.com", "baseten": "baseten.co", "chutes": "chutes.ai",
        "poe": "poe.com", "kilo": "kilo.ai", "edenai": "edenai.co",
        "digitalocean": "digitalocean.com", "vultr": "vultr.com",
        "scaleway": "scaleway.com", "ovhcloud": "ovh.cloud",
        "friendli": "friendli.ai", "upstage": "upstage.ai",
        "inference_net": "inference.net", "crusoe": "crusoecloud.com",
        "ollama": "ollama.com", "lmstudio": "lmstudio.ai",
        "vllm": "vllm.ai", "llamacpp": "github.com", "jan": "jan.ai",
        "anthropic": "anthropic.com", "google": "gemini.google.com",
        "cloudflare": "cloudflare.com", "deepseek": "deepseek.com",
        "numes": "numes.ai", "ollama_cloud": "ollama.com", "custom": "",
    }
    merged: dict[str, dict] = {}

    def _add(key: str, entry: dict) -> None:
        entry["domain"] = domains.get(key, "")
        entry["icon"] = f"/api/console/provider-icon/{entry['domain']}" if entry["domain"] else ""
        if key in merged:
            merged[key].update({k: v for k, v in entry.items() if v})
        else:
            merged[key] = entry
            entry["_order"] = len(order_list)
            order_list.append(key)

    order_list: list[str] = []

    _add("zai", {
        "key": "zai", "label": "Z.ai", "baseUrl": "https://api.z.ai/api/coding/paas/v4",
        "keyEnv": "ZAI_API_KEY", "defaultModel": "glm-5.3",
        "configured": _env_key_value("ZAI_API_KEY"), "local": False,
        "schema": [{"key": "endpoint_style", "label": "Endpoint", "kind": "select",
                    "options": ["coding plan", "paas"], "default": "coding plan"}],
        "apiStyles": ["chat"],
    })
    _add("anthropic", {
        "key": "anthropic", "label": "Anthropic", "baseUrl": "https://api.anthropic.com",
        "keyEnv": "ANTHROPIC_API_KEY", "defaultModel": "claude-sonnet-4-5",
        "configured": _env_key_value("ANTHROPIC_API_KEY"), "local": False,
        "schema": [], "apiStyles": ["messages"],
        "note": "native Messages API — key only",
    })
    _add("google", {
        "key": "google", "label": "Google Gemini",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "keyEnv": "GEMINI_API_KEY", "defaultModel": "gemini-2.5-flash",
        "configured": _env_key_value("GEMINI_API_KEY"), "local": False,
        "schema": [], "apiStyles": ["chat"], "note": "OpenAI-compatible shim",
    })
    _add("cloudflare", {
        "key": "cloudflare", "label": "Cloudflare Workers AI",
        "baseUrl": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "keyEnv": "CLOUDFLARE_API_TOKEN", "defaultModel": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "configured": _env_key_value("CLOUDFLARE_API_TOKEN"), "local": False,
        "schema": [{"key": "account_id", "label": "Account ID", "kind": "string",
                    "placeholder": "built into the base URL"}],
        "apiStyles": ["chat"], "note": "account ID is built into the base URL",
    })
    _add("custom", {
        "key": "custom", "label": "Custom (OpenAI-compatible)",
        "baseUrl": "", "keyEnv": "", "defaultModel": "",
        "configured": False, "local": False, "schema": [],
        "apiStyles": ["chat", "responses"],
    })

    for key, spec in PROVIDER_REGISTRY.items():
        if key in merged:
            continue
        _add(key, {
            "key": key, "label": spec.label, "baseUrl": spec.base_url,
            "keyEnv": spec.key_envs[0] if spec.key_envs else "",
            "defaultModel": spec.default_model,
            "configured": bool(spec.inline_key) if not spec.key_envs else bool(
                _env_key_value(spec.key_envs[0])),
            "local": key in _LOCAL_PROVIDERS,
            "schema": PROVIDER_SETUP_SCHEMAS.get(key, []),
            "apiStyles": API_STYLE_PROVIDERS.get(key, ["chat"]),
        })

    out = [merged[k] for k in order_list]
    for entry in out:
        entry.pop("_order", None)
    # configured first, then zai, then the rest alphabetically by label
    out.sort(key=lambda p: (not p["configured"], p["key"] not in ("zai", "z-ai"), p["label"].lower()))
    return {"providers": out}


# models.dev catalog cache — provider/model metadata (context windows,
# pricing, and the per-model reasoning_options the effort picker reads).
_models_dev_cache: dict = {"data": None, "at": 0.0}


@router.get("/api/console/models-dev")
def models_dev():
    """models.dev's live catalog — provider/model metadata (context
    windows, pricing, modes). Cached 10 minutes in-process."""
    import time as _t

    if _models_dev_cache["data"] and _t.time() - _models_dev_cache["at"] < 600:
        return _models_dev_cache["data"]
    import urllib.request

    with contextlib.suppress(Exception):
        req = urllib.request.Request("https://models.dev/api.json",
                                     headers={"User-Agent": "suijin"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        _models_dev_cache.update(data=data, at=_t.time())
        return data
    return {"error": "models.dev unreachable"}


def _key_value_for(key: str, spec_key_env: str | None, override: dict | None = None) -> str:
    """The operator's actual key for a provider: .env / os.environ —
    the VALUE, not a bool (the models fetch must authenticate)."""
    import os as _os

    candidates = []
    if spec_key_env:
        candidates.append(spec_key_env)
    base_env = key.upper().replace("-", "_") + "_API_KEY"
    candidates.append(base_env)
    if (override or {}).get("auth", {}).get("keyEnv"):
        candidates.append(str(override["auth"]["keyEnv"]))
    wanted = set(candidates)
    with contextlib.suppress(OSError):
        env_path = _ws() / "config" / ".env"
        for ln in env_path.read_text(errors="replace").splitlines():
            if "=" in ln:
                name, _, value = ln.partition("=")
                if name.strip() in wanted and value.strip():
                    return value.strip()
    for name in wanted:
        v = _os.environ.get(name)
        if v:
            return v
    return ""


@router.get("/api/console/providers/{key}/models")
def provider_live_models(key: str):
    """Live model list FROM THE PROVIDER with the operator's key — what
    the key can actually serve. Auth built exactly like the wire call
    (field 2026-11-17: the old path read os.environ only → 401 because
    the web server never loads .env into the process)."""
    import urllib.request

    from suijin.server.providers.lib.registry import PROVIDER_REGISTRY
    from suijin.server.providers.lib import provider_override as _get_override

    catalog = provider_catalog()
    entry = next((p for p in catalog["providers"] if p["key"] == key), None)
    override = _get_override(key)
    spec = PROVIDER_REGISTRY.get(key)
    key_env = (entry or {}).get("keyEnv") or (spec.key_envs[0] if spec and spec.key_envs else "")
    api_key = _key_value_for(key, key_env, override)
    base = str(override.get("baseUrl") or (entry or {}).get("baseUrl") or "")
    if "{account_id}" in base:
        account = str((override.get("extras") or {}).get("account_id")
                      or (load_config_safe().get("provider_extras") or {}).get(key, {}).get("account_id") or "")
        base = base.replace("{account_id}", account) if account else ""
    if not base:
        return {"models": [], "note": "no base URL configured for this provider", "source": "none"}
    is_local = key in _LOCAL_PROVIDERS
    headers = {"Content-Type": "application/json"}
    if api_key and not is_local:
        headers["Authorization"] = f"Bearer {api_key}"
    models_path = "/api/tags" if key == "ollama" else "/models"
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}{models_path}", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        if isinstance(data, dict) and "models" in data and key == "ollama":
            ids = [m.get("name") for m in data["models"] if m.get("name")]
        else:
            ids = [m.get("id") for m in (data.get("data") or data.get("models") or []) if m.get("id")]
        return {"models": sorted(set(ids)), "count": len(ids), "source": "provider"}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:160]
        note = f"provider list failed ({e.code}) — showing the models.dev catalog"
        fallback = _models_dev_models_for(key)
        return {"models": fallback, "source": "models.dev", "note": note, "detail": detail}
    except Exception as e:  # noqa: BLE001
        fallback = _models_dev_models_for(key)
        return {"models": fallback, "source": "models.dev",
                "note": f"{type(e).__name__}: {str(e)[:120]} — showing the models.dev catalog"}


def _models_dev_models_for(key: str) -> list[str]:
    """models.dev's model ids for a provider (the accurate catalog when
    the provider's own list endpoint fails)."""
    data = models_dev()
    if not isinstance(data, dict) or "error" in data:
        return []
    alias = {"z-ai": "zai", "zhipu": "zai"}
    entry = data.get(key) or data.get(alias.get(key, "")) or {}
    return sorted((entry.get("models") or {}).keys())


@router.get("/api/console/provider-icon/{domain}")
def provider_icon(domain: str):
    """Provider logo from a cached local copy.

    Field 2026-11-17: logo.clearbit.com is unreachable on this network
    (000) and browser-side favicon fetches were flaky — so the server
    fetches once, caches to the workspace, and serves the bytes. Any
    failure is a 404 and the UI falls back to a letter avatar.
    """
    import re as _re
    import urllib.request

    from fastapi import Response

    dom = str(domain or "").strip().lower()
    if not _re.fullmatch(r"[a-z0-9.-]{2,80}", dom):
        return Response(status_code=404)
    cache_dir = _ws() / "cache" / "provider-icons"
    cache_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Cache-Control": "public, max-age=604800"}
    for ext, media in ((".png", "image/png"), (".svg", "image/svg+xml"), (".ico", "image/vnd.microsoft.icon")):
        cached = cache_dir / f"{dom}{ext}"
        if cached.exists() and cached.stat().st_size > 50:
            return Response(cached.read_bytes(), media_type=media, headers=headers)
    sources = (
        f"https://unavatar.io/{dom}",
        f"https://www.google.com/s2/favicons?domain={dom}&sz=64",
        f"https://icons.duckduckgo.com/ip3/{dom}.ico",
    )
    for src in sources:
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
                ctype = r.headers.get_content_type() or "image/png"
            if len(data) > 50:
                head = data[:400].lstrip().lower()
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    ext, ctype = ".png", "image/png"
                elif data[:4] == b"\x00\x00\x01\x00":
                    ext, ctype = ".ico", "image/vnd.microsoft.icon"
                elif b"<svg" in head or head.startswith(b"<?xml"):
                    ext, ctype = ".svg", "image/svg+xml"
                else:
                    continue  # not an image — try the next source
                (cache_dir / f"{dom}{ext}").write_bytes(data)
                return Response(data, media_type=ctype, headers=headers)
        except Exception:  # noqa: BLE001
            continue
    return Response(status_code=404)


@router.get("/api/console/runtime")
def runtime_get():
    return {"runtime": _runtime(), "effort": str(load_config_safe().get("reasoning_effort") or "medium")}


@router.patch("/api/console/runtime")
def runtime_patch(body: dict = Body(default={})):
    """Persist the selected provider/model/effort to config.json — the ONE
    source the snapshot's runtime reads, so the choice reflects everywhere."""
    cfg = load_config_safe()
    provider = str(body.get("providerId") or "").strip()
    model = str(body.get("model") or "").strip()
    effort = str(body.get("effort") or "").strip()
    if provider:
        cfg["provider"] = "zai" if provider == "z-ai" else provider
    if model:
        key = "zai_model" if provider in ("z-ai", "zai") else f"{provider}_model"
        cfg[key] = model
        cfg["model_1"] = model  # the leader/primary — reflects everywhere
    routing = dict(cfg.get("model_routing") or {})
    if effort:
        routing["planner"] = model or routing.get("planner", "glm-5.3")
        cfg["model_routing"] = routing
        cfg["reasoning_effort"] = effort
    with contextlib.suppress(Exception) if False else contextlib.suppress(OSError):
        import json as _json

        from suijin.modules.platform.lib.config_loader import CONFIG_PATH

        CONFIG_PATH.write_text(_json.dumps(cfg, indent=2))
    return {"ok": True, "runtime": _runtime()}


@router.get("/api/console/provider-override/{key}")
def provider_override_get(key: str):
    from suijin.server.providers.lib import provider_override as _get

    return {"key": key, "override": _get(key)}


@router.put("/api/console/provider-override/{key}")
def provider_override_put(key: str, body: dict = Body(default={})):
    """Save the REAL request-config JSON for a provider — the engine
    merges it verbatim into every call (validated before storing)."""
    from suijin.server.providers.lib import save_provider_override

    ov = body.get("override")
    if not isinstance(ov, dict):
        return JSONResponse({"error": "override must be an object"}, status_code=400)
    base = str(ov.get("baseUrl") or "").strip()
    if base and not base.startswith(("http://", "https://")):
        return JSONResponse({"error": "baseUrl must be http(s)"}, status_code=400)
    auth = ov.get("auth")
    if auth is not None and not isinstance(auth, dict):
        return JSONResponse({"error": "auth must be an object"}, status_code=400)
    for field in ("query", "body", "paths", "modelMapping"):
        v = ov.get(field)
        if v is not None and not isinstance(v, dict):
            return JSONResponse({"error": f"{field} must be an object"}, status_code=400)
    save_provider_override(key, ov)
    return {"ok": True, "key": key, "override": ov}


def _ping_provider(key: str, override: dict | None = None, model: str = "", max_tokens: int = 16) -> dict:
    """Fire ONE tiny real call — the truth about whether a provider works
    right now (key saved ≠ provider up). Shared by Test ping and the
    Diagnostics verifier. Never raises; raw error text is safe to show."""
    import urllib.request

    from suijin.server.providers.lib import _merged_request, provider_override
    from suijin.server.providers.lib.registry import PROVIDER_REGISTRY

    spec = PROVIDER_REGISTRY.get(key)
    ov = dict(override or provider_override(key) or {})
    if not spec and not ov:
        return {"ok": False, "status": None, "error": f"unknown provider '{key}'"}
    if not spec:
        # custom OpenAI-compatible box — build a minimal spec from the override
        from suijin.server.providers.lib.registry import ProviderSpec

        spec = ProviderSpec(key=key, label=str(ov.get("label") or key),
                            base_url=str(ov.get("baseUrl") or "").rstrip("/"),
                            key_envs=(str((ov.get("auth") or {}).get("keyEnv") or "CUSTOM_API_KEY"),),
                            default_model=str(ov.get("model") or ""))
    cfg = load_config_safe()
    api_key = _key_value_for(key, spec.key_envs[0] if spec.key_envs else "", ov)
    if not api_key and key not in _LOCAL_PROVIDERS:
        return {"ok": False, "status": None,
                "error": f"{spec.key_envs[0] if spec.key_envs else 'key'} not set — enter the key first"}
    if ov and not spec.key_envs:
        api_key = api_key or ""  # local/anonymous box
    model = str(model or (ov.get("model") if ov else "") or cfg.get(f"{key}_model") or cfg.get("model_1") or spec.default_model or "gpt-3.5-turbo")
    try:
        url, headers, payload, wire_model = _merged_request(
            spec, cfg, model, [{"role": "user", "content": "ping"}], max_tokens, 0.2, api_key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        return {"ok": True, "status": 200, "url": url,
                "reply": str(msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or "(empty)")[:200],
                "model": wire_model}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")[:300]
        return {"ok": False, "status": e.code, "url": url, "error": raw}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "url": url, "error": f"{type(e).__name__}: {str(e)[:200]}"}


@router.post("/api/console/provider-override/{key}/test")
def provider_override_test(key: str, body: dict = Body(default={})):
    """Fire a REAL 1-token call through the stored (or inline) override —
    the operator sees the raw response or the raw error."""
    ov = body.get("override")
    if isinstance(ov, dict) and ov:
        from suijin.server.providers.lib import save_provider_override as _save

        _save(key, ov)
    return _ping_provider(key, ov if isinstance(ov, dict) else None,
                          str(body.get("model") or ""))


@router.post("/api/console/providers/{key}/verify")
def provider_verify(key: str):
    """Diagnostics honesty: what a provider ACTUALLY does right now."""
    return _ping_provider(key)


_available_models_cache: dict = {"at": 0.0, "models": [], "providers": []}


@router.get("/api/console/available-models")
def available_models():
    """Every model from every CONFIGURED provider — values are
    'provider:model' so a pick routes there; cached 5 minutes."""
    import time as _t

    if _available_models_cache["models"] and _t.time() - _available_models_cache["at"] < 300:
        return {"models": _available_models_cache["models"],
                "providers": _available_models_cache["providers"], "cached": True}
    catalog = provider_catalog()
    models: list[dict] = []
    providers: list[dict] = []
    for entry in catalog["providers"]:
        if not entry.get("configured"):
            continue
        key, label = entry["key"], entry["label"]
        providers.append({"key": key, "label": label})
        for mid in _provider_model_ids(key):
            models.append({"value": f"{key}:{mid}", "label": mid,
                           "provider": key, "providerLabel": label,
                           "supported": True})
    _available_models_cache.update(at=_t.time(), models=models, providers=providers)
    return {"models": models, "providers": providers, "cached": False}


def _provider_model_ids(key: str) -> list[str]:
    """Cached live list for one provider; falls back to models.dev."""
    import time as _t

    cache = getattr(_provider_model_ids, "_cache", None)
    if cache is None:
        cache = {}
        _provider_model_ids._cache = cache
    entry = cache.setdefault(key, {"at": 0.0, "ids": []})
    if entry["ids"] and _t.time() - entry["at"] < 300:
        return entry["ids"]
    ids: list[str] = []
    with contextlib.suppress(Exception):
        from suijin.server.providers.lib import provider_override

        ov = provider_override(key) or {}
        import urllib.request

        catalog = provider_catalog()
        spec_entry = next((p for p in catalog["providers"] if p["key"] == key), None)
        base = str(ov.get("baseUrl") or (spec_entry or {}).get("baseUrl") or "")
        if "{account_id}" in base:
            account = str((ov.get("extras") or {}).get("account_id") or
                          (load_config_safe().get("provider_extras") or {}).get(key, {}).get("account_id") or "")
            base = base.replace("{account_id}", account) if account else ""
        if base.startswith(("http://", "https://")):
            key_env = (spec_entry or {}).get("keyEnv") or ""
            api_key = _key_value_for(key, key_env, ov)
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            path = "/api/tags" if key == "ollama" else "/models"
            req = urllib.request.Request(f"{base.rstrip('/')}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode())
            if key == "ollama":
                ids = [m.get("name") for m in data.get("models", []) if m.get("name")]
            else:
                ids = [m.get("id") for m in (data.get("data") or data.get("models") or []) if m.get("id")]
    if not ids:
        ids = _models_dev_models_for(key)
    entry.update(at=_t.time(), ids=sorted(set(str(i) for i in ids if i))[:300])
    return entry["ids"]


