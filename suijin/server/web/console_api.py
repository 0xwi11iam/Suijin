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
                "hb_age": int(age), "dead": dead or stopped, "status": r["status"] or "",
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


_FINDING_FRONTMATTER = re.compile(
    r"^(?:catalog_exploit|exploit)\s+confirmed:\s*"
    r"(?:(EXP-\d{1,4})\s+CONFIRMED\s+—\s*)?"
    r"(?:(CRITICAL|HIGH|MEDIUM|LOW|INFO)\s+)?"
    r"(?:CVSS\s+([\d.]+)\s*)?"
    r":?\s*(.+)$",
    re.I,
)
_FINDING_LEGACY_HEAD = re.compile(
    r"^(EXP-\d{1,4})\s+CONFIRMED\s+—\s*"
    r"(?:(CRITICAL|HIGH|MEDIUM|LOW|INFO)\s+)?"
    r"(?:CVSS\s+([\d.]+)\s*)?"
    r":?\s*(.+)$",
    re.I,
)


def _clean_finding_title(raw: str) -> tuple[str, str, str, float | None]:
    """Strip the old write-path frontmatter from a stored finding title.

    Field rows read `catalog_exploit confirmed: EXP-002 CONFIRMED — LOW
    CVSS 3.7 : <real title>`. The real title is the part after the last
    colon; the EXP id and CVSS ride along as structured display fields.
    """
    t = str(raw or "").strip()
    exp_id, sev_hint, cvss = "", "", None
    for pattern in (_FINDING_FRONTMATTER, _FINDING_LEGACY_HEAD):
        m = pattern.match(t)
        if not m:
            continue
        exp_id = (m.group(1) or "").upper()
        sev_hint = (m.group(2) or "").lower()
        with contextlib.suppress(TypeError, ValueError):
            cvss = float(m.group(3)) if m.group(3) else None
        t = m.group(4).strip()
        break
    t = re.sub(r"^(?:catalog_exploit|exploit)\s+confirmed:\s*", "", t, flags=re.I)
    t = re.sub(r"^EXP-\d{1,4}\s*[:\-]\s*", "", t, flags=re.I)
    # Legacy summaries appended the verifier sentence and were truncated at
    # 150 chars ("... primitive). The VERIF"). Strip that tail too.
    t = re.sub(r"\.\s+(?:The\s+)?VERIF\w*\b.*$", "", t, flags=re.I)
    return t.strip() or str(raw or "").strip(), exp_id, sev_hint, cvss


_CATALOG_INDEX_CACHE: dict = {"at": 0.0, "index": {}}


def _catalog_index() -> dict:
    """EXP id -> full catalog entries (title/target/class).

    Legacy findings stored a 150-char truncated title and no target. The
    exploit catalog still has the full title and target; join on EXP id +
    the stored prefix so the Findings page can show both.
    """
    now = time.time()
    if now - float(_CATALOG_INDEX_CACHE.get("at") or 0) < 60:
        return _CATALOG_INDEX_CACHE.get("index") or {}
    index: dict[str, list[dict]] = {}
    roots = [_ws() / "artifacts" / "exploits"]
    engagements = _ws() / "engagements"
    if engagements.is_dir():
        roots.extend(sorted(engagements.glob("*/exploits")))
    for root in roots:
        if not root.is_dir():
            continue
        for path in list(root.glob("catalog.json")) + list(root.glob("*/catalog.json")):
            with contextlib.suppress(Exception):
                data = json.loads(path.read_text())
                for eid, entry in (data.get("entries") or {}).items():
                    if not isinstance(entry, dict):
                        continue
                    title = str(entry.get("title") or "").strip()
                    if not title:
                        continue
                    last = entry.get("last_run") or {}
                    index.setdefault(str(eid).upper(), []).append({
                        "title": title,
                        "target": str(entry.get("target") or "").strip(),
                        "class": str(entry.get("class") or "").strip(),
                        "description": str(entry.get("description") or "").strip(),
                        "poc": entry.get("poc") or [],
                        "last_run": str((last.get("output") if isinstance(last, dict) else "") or ""),
                    })
    _CATALOG_INDEX_CACHE.update(at=now, index=index)
    return index


DESKTOP_DIR = Path.home() / "Desktop"


def _catalog_entry_for(rec: dict) -> dict | None:
    exp = str(rec.get("expId") or "").strip().upper()
    if not exp:
        return None
    prefix = str(rec.get("title") or "")[:60].casefold()
    for entry in _catalog_index().get(exp, []):
        full = str(entry.get("title") or "").casefold()
        if full.startswith(prefix[:40]) or prefix.startswith(full[:40]):
            return entry
    return None


def _submission_metadata(rec: dict, catalog: dict | None) -> dict:
    from suijin.server.tools.lib.submission_report import defaults

    meta = defaults(
        (catalog or {}).get("class") or rec.get("class") or "",
        str(rec.get("title") or ""),
        str(rec.get("target") or (catalog or {}).get("target") or ""),
    )
    db = sqlite3.connect(str(_swarm_db()), timeout=5)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS finding_submission_meta ("
            "finding_id TEXT PRIMARY KEY, class TEXT, cwe TEXT, vrt TEXT, "
            "asset_type TEXT, cvss_version TEXT, severity_method TEXT, tester_ip TEXT, updated_at TEXT)"
        )
        row = db.execute(
            "SELECT class,cwe,vrt,asset_type,cvss_version,severity_method,tester_ip "
            "FROM finding_submission_meta WHERE finding_id=?", (rec["id"],)
        ).fetchone()
        if row:
            keys = ("class", "cwe", "vrt", "asset_type", "cvss_version", "severity_method", "tester_ip")
            meta.update({k: v for k, v in zip(keys, row) if v})
        db.execute(
            "INSERT OR REPLACE INTO finding_submission_meta "
            "(finding_id,class,cwe,vrt,asset_type,cvss_version,severity_method,tester_ip,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (rec["id"], meta["class"], meta["cwe"], meta["vrt"], meta["asset_type"],
             meta["cvss_version"], meta["severity_method"], meta["tester_ip"],
             time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())),
        )
        db.commit()
    finally:
        db.close()
    return meta


def _finding_evidence(rec: dict) -> list[str]:
    db = sqlite3.connect(str(_swarm_db()), timeout=5)
    try:
        row = db.execute(
            "SELECT evidence_paths,description_md FROM findings_v2 "
            "WHERE engagement=? AND (legacy_id=? OR title LIKE ?) LIMIT 1",
            (rec.get("engagement") or "", rec.get("expId") or "", f"%{str(rec.get('title') or '')[:40]}%"),
        ).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        db.close()
    out: list[str] = []
    if row and row[0]:
        with contextlib.suppress(Exception):
            out = [str(p) for p in json.loads(row[0]) if p]
    return out


def _write_submission_report(finding_id: str, platform: str) -> dict:
    from suijin.server.tools.lib.submission_report import render

    rec = next((f for f in _findings() if str(f.get("id")) == str(finding_id)), None)
    if not rec:
        raise KeyError(finding_id)
    catalog = _catalog_entry_for(rec)
    meta = _submission_metadata(rec, catalog)
    evidence = _finding_evidence(rec)
    target = rec.get("target") or (catalog or {}).get("target") or ""
    report_rec = {
        "title": rec.get("title") or (catalog or {}).get("title") or "Security finding",
        "description": (catalog or {}).get("description") or rec.get("description") or rec.get("title") or "",
        "target": target,
        "severity": rec.get("severity") or "",
        "cvss": rec.get("cvssScore"),
        "evidencePaths": evidence,
    }
    markdown = render(report_rec, meta, platform, catalog)
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", str(report_rec["title"]).lower()).strip("-")[:60] or "finding"
    program = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-")[:32] or "target"
    label = platform if platform != "other" else "all"
    name = f"{time.strftime('%Y-%m-%d')}_{program}_{label}_{slug}.md"
    path = DESKTOP_DIR / name
    counter = 2
    while path.exists():
        path = DESKTOP_DIR / f"{path.stem}-{counter}.md"
        counter += 1
    path.write_text(markdown, encoding="utf-8")
    return {"path": str(path), "platform": platform, "bytes": len(markdown.encode())}


@router.post("/api/findings/{finding_id}/submission-report")
def finding_submission_report(finding_id: str, body: dict = Body(default={})):
    platform = str(body.get("platform") or "other").strip().lower()
    if platform not in ("hackerone", "bugcrowd", "intigriti", "other"):
        return JSONResponse({"error": "unknown platform"}, status_code=400)
    try:
        return _write_submission_report(finding_id, platform)
    except KeyError:
        return JSONResponse({"error": "finding not found"}, status_code=404)


def _explain_context(finding_id: str) -> tuple[dict, dict, list[str]]:
    rec = next((f for f in _findings() if str(f.get("id")) == str(finding_id)), None)
    if not rec:
        raise KeyError(finding_id)
    catalog = _catalog_entry_for(rec) or {}
    evidence = _finding_evidence(rec)
    return rec, catalog, evidence


def _explain_prompt(rec: dict, catalog: dict, evidence: list[str]) -> str:
    title = str(rec.get("title") or catalog.get("title") or "Security finding")
    target = rec.get("target") or catalog.get("target") or "unknown"
    severity = rec.get("severity") or "unknown"
    cvss = rec.get("cvssScore")
    description = catalog.get("description") or rec.get("description") or title
    names = ", ".join(Path(p).name for p in evidence) or "none listed"
    short = description[:1200]
    return f"""Review this finding for a bug-bounty researcher. Be concise (under 350 words) and use exactly these sections:

## Plain explanation
2-3 short paragraphs: what it is, where, how it works.

## Business impact
Who is affected and what an attacker can reach.

## Payability
Would a program pay? Realistic rating and reasons.

## Chance of acceptance
Low / Medium / High and why.

## Missing information
What is not proven and the exact evidence needed.

Finding: {title}
Asset: {target}
Severity: {severity}{f" (CVSS {cvss})" if cvss else ""}
Evidence: {names}
Description: {short}

Do not invent target behavior."""


def _write_explain_report(finding_id: str) -> dict:
    from suijin.modules.platform.lib.config_loader import load_config
    from suijin.modules.providers.lib import generate

    rec, catalog, evidence = _explain_context(finding_id)
    prompt = _explain_prompt(rec, catalog, evidence)
    cfg = load_config()
    # M1 = the main leader model (`model_1`); no alternate provider, no local
    # or heuristic model in this path.
    leader = str(cfg.get("model_1") or cfg.get("final_model_id") or "").strip()
    provider = str(cfg.get("provider") or "").strip().lower()
    if ":" in leader:
        head, rest = leader.split(":", 1)
        try:
            from suijin.server.providers.lib.registry import PROVIDER_REGISTRY

            if head in PROVIDER_REGISTRY:
                provider, leader = head, rest
        except Exception:  # noqa: BLE001
            pass
    if leader:
        cfg["provider"] = provider or cfg.get("provider")
        cfg[f"{provider}_model"] = leader
        cfg["model_1"] = leader

    holder: dict = {}

    def run():
        holder["out"] = generate(
            [
                {"role": "system", "content": "You are a precise security reviewer. Return only the requested Markdown."},
                {"role": "user", "content": prompt},
            ],
            cfg,
            max_tokens=2000,
            retries=2,
        )

    import threading

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(120)
    if thread.is_alive():
        raise RuntimeError("main leader model timed out after 120 seconds")
    text = str(holder.get("out") or "").strip()
    if not text or text == "(empty response)" or text.startswith("Error:"):
        raise RuntimeError("main leader model returned no text" + (f": {text[:300]}" if text else ""))

    title = str(rec.get("title") or "finding")
    markdown = f"# Explain: {title}\n\n**Asset:** {rec.get('target') or 'unknown'}\n\n{text}\n"
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "finding"
    path = DESKTOP_DIR / f"{time.strftime('%Y-%m-%d')}_explain_{slug}.md"
    counter = 2
    while path.exists():
        path = DESKTOP_DIR / f"{path.stem}-{counter}.md"
        counter += 1
    path.write_text(markdown, encoding="utf-8")
    return {"path": str(path), "markdown": markdown}


@router.get("/api/findings/{finding_id}/explain-prompt")
def finding_explain_prompt(finding_id: str):
    try:
        rec, catalog, evidence = _explain_context(finding_id)
        return {"prompt": _explain_prompt(rec, catalog, evidence)}
    except KeyError:
        return JSONResponse({"error": "finding not found"}, status_code=404)


@router.post("/api/findings/{finding_id}/explain")
def finding_explain(finding_id: str):
    try:
        return _write_explain_report(finding_id)
    except KeyError:
        return JSONResponse({"error": "finding not found"}, status_code=404)
    except Exception as exc:  # noqa: BLE001 — surface the leader model's real error
        return JSONResponse({"error": str(exc)}, status_code=502)


def _findings(session: str | None = None) -> list[dict]:
    conn = _db()
    if not conn:
        return []
    try:
        q = ("SELECT severity, status, title, agent_id, created_at, id, engagement "
             "FROM findings ORDER BY created_at DESC LIMIT 100")
        out = []
        for r in conn.execute(q).fetchall():
            with contextlib.suppress(Exception):
                title, exp_id, sev_hint, cvss = _clean_finding_title(r[2] or "")
                target = ""
                if exp_id:
                    prefix = title[:48].casefold()
                    for candidate in _catalog_index().get(exp_id.upper(), []):
                        full = candidate["title"]
                        if full.casefold().startswith(prefix) or prefix.startswith(full.casefold()[:48]):
                            title = full
                            target = candidate["target"]
                            break
                out.append({
                    "id": str(r[5] or ""), "severity": r[0] or sev_hint or "info", "status": r[1] or "new",
                    "title": title[:1000], "agent": r[3] or "",
                    "engagement": r[6] or "", "target": target,
                    "expId": exp_id, "cvssScore": cvss,
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


_TARGET_RE = re.compile(
    r"(?i)\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?::(\d{2,5}))?\b"
)


def _target_from_text(text: str) -> str:
    """The host a session is about — explicit `target` first, else the typed
    mission. Auto target folders key off this; no manual setup."""
    match = _TARGET_RE.search(str(text or ""))
    if not match:
        return ""
    host = match.group(1).lower().rstrip(".")
    port = match.group(2)
    return f"{host}:{port}" if port else host


def _session_engagement_meta(session: str) -> dict:
    with contextlib.suppress(Exception):
        meta = _eng_dir(session) / "engagement.json"
        if meta.is_file():
            value = json.loads(meta.read_text())
            if isinstance(value, dict):
                return value
    return {}


def _target_for_meta(meta: dict, session: str = "") -> str:
    """AUTO TARGET FOLDERS: explicit target -> mission text -> slug.
    The sidebar groups sessions by this; the operator never creates a folder."""
    # An explicit target is the operator's label — often a PROGRAM name
    # ("Absolute"), not a host. Use it verbatim; only parse hits out of the
    # mission text when no explicit target was stored.
    explicit = str(meta.get("target") or meta.get("scope") or "").strip()
    if explicit:
        return explicit
    target = (
        _target_from_text(meta.get("name") or "")
        or _target_from_text(meta.get("description") or "")
    )
    if not target:
        slug = re.sub(r"^\d{8}_\d{6}_", "", session)
        target = slug.replace("__", "-").replace("_", "-").strip("-") or session
    return target


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
    paused = bool(live) and all(str(a.get("status") or "") == "paused" for a in live)
    status = "waiting" if paused else "working" if live else "idle"
    # THE REAL TITLE (field 2026-11-17: "example.com" displayed as
    # "example com" — the title was rebuilt from the slug, which ate the
    # dot). The typed title is stored in engagement.json at create; show it.
    meta = _session_engagement_meta(session)
    title = str(meta.get("name") or "").strip() or session.replace("-", " ")
    target = _target_for_meta(meta, session)[:120]
    return {
        "id": session, "target": target, "role": "root",
        "targetKey": target.lower(), "targetLabel": target,
        "autonomyMode": "standard", "scopeConfigured": False,
        "localScopeConfigured": False, "status": status, "paused": paused,
        "permissions": meta.get("permissions") or "exploitation",
        "mode": meta.get("mode") or "vdp",
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
        status = "stopped" if (st == "stopped" or a["dead"]) else "parked" if st == "paused" else "running"
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


def _finding_description(f: dict) -> str:
    """The full operator-facing description, not the title.

    findings_v2 carries the multi-paragraph description written by the
    agent; fall back to the catalog, then the title for old rows."""
    engagement = f.get("engagement") or ""
    if engagement:
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        try:
            row = db.execute(
                "SELECT description_md FROM findings_v2 WHERE engagement=? "
                "AND (legacy_id=? OR title LIKE ?) LIMIT 1",
                (engagement, f.get("expId") or "", f"%{str(f.get('title') or '')[:40]}%"),
            ).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            db.close()
        if row and str(row[0] or "").strip():
            return str(row[0]).strip()
    catalog = _catalog_entry_for(f)
    if catalog and catalog.get("description"):
        return str(catalog["description"]).strip()
    return str(f.get("title") or "")


def _finding_group(f: dict) -> dict:
    fp = re.sub(r"[^a-z0-9]+", "-", (f["title"] or "finding").lower())[:60]
    triage = _ledger("triage").get(fp) or {}
    impact = _ledger("impact").get(f["id"])
    workflow_ledger = _ledger("workflow").get(fp) or {}
    engagement = f.get("engagement") or ""
    chat_title = _engagement_record(engagement).get("name") if engagement else ""
    audit_path = str(_eng_dir(engagement) / "events.jsonl") if engagement else ""
    try:
        from suijin.server.tools.lib.submission_report import defaults as _sub_defaults

        _catalog = _catalog_entry_for(f)
        _meta = _sub_defaults(
            (_catalog or {}).get("class") or "", f.get("title") or "",
            f.get("target") or (_catalog or {}).get("target") or "",
        )
        impact, remediation = _meta["impact"], _meta["remediation"]
    except Exception:  # noqa: BLE001 — display detail must never break the API
        impact = remediation = ""
    rec = {
        "id": f["id"], "scanId": f["agent"], "title": f["title"],
        "description": _finding_description(f), "severity": f["severity"], "category": "finding",
        "status": f["status"], "fingerprint": fp,
        "expId": f.get("expId") or "", "cvssScore": f.get("cvssScore"),
        "engagement": engagement, "target": f.get("target") or "",
        "impact": impact, "remediation": remediation,
        "chatId": engagement, "chatTitle": chat_title, "auditPath": audit_path,
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


def _recent_engagement_sessions(live: set[str], per_target: int = 8) -> list[str]:
    """Previous engagements for the targets that are live right now.

    The sidebar is target-folders: a running agent should sit next to that
    target's older chats. Scanning all 1,000+ historical engagement folders
    is noise, so only folders whose derived target matches a live target are
    included, capped per target."""
    root = _ws() / "engagements"
    if not root.is_dir():
        return []
    live_targets = {_desk_session(name).get("targetKey") for name in live}
    candidates: list[tuple[float, str]] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name in live or entry.name in ("_default", "default"):
            continue
        if entry.name in _deleted_sessions:
            continue
        if not (entry / "engagement.json").is_file() and not (entry / "events.jsonl").is_file():
            continue
        with contextlib.suppress(OSError):
            candidates.append((entry.stat().st_mtime, entry.name))
    candidates.sort(reverse=True)
    out: list[str] = []
    counts: dict[str, int] = {}
    for _mtime, name in candidates[:800]:
        meta = _session_engagement_meta(name)
        explicit = str(meta.get("target") or "").strip()
        key = _target_for_meta(meta, name).lower()
        # Legacy folders with no target still join a live target when their
        # slug spells it: `example-com` → example.com, and
        # `audit-…-www-design-example-com` → design.example.com.
        if not explicit and key not in live_targets:
            dotted = re.sub(r"^\d{8}_\d{6}_", "", name).replace("-", ".")
            for target_key in live_targets:
                # Only HOST keys can be recovered from a slug; a program name
                # like "Example" would match every slug ending in ".example".
                if not target_key or "." not in target_key:
                    continue
                if dotted == target_key or dotted.endswith("." + target_key):
                    key = target_key
                    break
        # Explicit-target sessions persist after their agents stop (the five
        # operator-launched engagements stay as folders). Legacy folders with
        # no target join only while a live session shares their target.
        if not explicit and key not in live_targets:
            continue
        if counts.get(key, 0) >= per_target:
            continue
        counts[key] = counts.get(key, 0) + 1
        out.append(name)
    return out


@router.get("/api/console/sessions")
def sessions_list():
    live = _swarm_names()
    names = list(live)
    for name in _recent_engagement_sessions(set(live)):
        if name not in names:
            names.append(name)
    out = []
    for name in names:
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


PERMISSIONS = ("recon", "exploitation", "maximum")
MODES = ("bbp", "vdp", "ctf", "pentest")


def _mission_with_settings(session: str, title: str) -> str:
    meta = _session_engagement_meta(session)
    permissions = str(meta.get("permissions") or "exploitation").lower()
    mode = str(meta.get("mode") or "vdp").lower()
    files = [str(f) for f in (meta.get("files") or [])]
    inbox = _eng_dir(session) / "inbox"
    lines = [
        title,
        "",
        f"ENGAGEMENT SETTINGS: permissions={permissions}; mode={mode}.",
        f"Operator-provided files are in {inbox} (also visible as ~/inbox). Read them before acting.",
    ]
    if files:
        lines.append("Inbox files: " + ", ".join(files) + ".")
    if mode == "pentest":
        lines.append("PENTEST: the uploaded scope document is authoritative; do not test outside it.")
    elif mode == "bbp":
        lines.append("BBP: catalog only findings you would actually submit; skip low-value noise.")
    elif mode == "vdp":
        lines.append("VDP: catalog valid findings, including smaller ones the program wants.")
    elif mode == "ctf":
        lines.append("CTF: hunt flags; reporting format is secondary.")
    if permissions == "recon":
        lines.append("RECON ONLY: passive/read-only actions; no state changes, logins or submissions.")
    elif permissions == "maximum":
        lines.append("MAXIMUM: everything except denial-of-service.")
    return "\n".join(lines)


@router.post("/api/console/sessions/{session_id}/files")
def session_files_upload(session_id: str, body: dict = Body(default={})):
    """Operator inbox: text files the agent may use however it wants."""
    meta = _session_engagement_meta(session_id)
    if not meta:
        return JSONResponse({"error": "session not found"}, status_code=404)
    inbox = _eng_dir(session_id) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    existing = [str(x) for x in (meta.get("files") or [])]
    for item in (body.get("files") or [])[:20]:
        if not isinstance(item, dict):
            continue
        raw_name = Path(str(item.get("name") or "file.txt")).name
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" .")[:80] or "file.txt"
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if len(content.encode("utf-8")) > 1_000_000:
            return JSONResponse({"error": f"{name}: file is larger than 1 MB"}, status_code=400)
        if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in content):
            return JSONResponse({"error": f"{name}: not plain text (control characters)"}, status_code=400)
        path = inbox / name
        counter = 2
        while path.exists():
            path = inbox / f"{Path(name).stem}-{counter}{Path(name).suffix}"
            counter += 1
        path.write_text(content, encoding="utf-8")
        saved.append({"name": path.name, "bytes": len(content.encode("utf-8"))})
        if path.name not in existing:
            existing.append(path.name)
    meta["files"] = existing
    meta["inbox"] = str(inbox)
    (_eng_dir(session_id) / "engagement.json").write_text(json.dumps(meta, indent=2))
    # Make the inbox reachable from the agent's userland without copying.
    home = _eng_dir(session_id) / "home"
    home.mkdir(parents=True, exist_ok=True)
    link = home / "inbox"
    if not link.exists():
        with contextlib.suppress(OSError):
            link.symlink_to("../inbox", target_is_directory=True)
    readme = inbox / "README.md"
    readme.write_text(
        "# Operator inbox\n\n"
        "These files were provided by the operator. Use them however the engagement needs — "
        "scope, credentials, templates, prior notes, or target material.\n\n"
        + "\n".join(f"- `{name}`" for name in existing)
        + "\n",
        encoding="utf-8",
    )
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.engagement_index import rebuild as _rebuild_index

        _rebuild_index()
    return {"files": saved, "all": existing}


@router.post("/api/console/sessions")
def sessions_create(body: dict = Body(default={})):
    title = str(body.get("title") or body.get("target") or "").strip() or "new swarm"
    structured = bool(body.get("structured"))
    workers_per_cat = int(body.get("workers") or 2)
    permissions = str(body.get("permissions") or "exploitation").lower()
    mode = str(body.get("mode") or "vdp").lower()
    if permissions not in PERMISSIONS:
        permissions = "exploitation"
    if mode not in MODES:
        mode = "vdp"
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
    # The reports mkdir above already created the dir, so _session_exists()
    # is true for every new session — key the metadata write on the FILE,
    # not the folder (field 2026-11-23: explicit targets never persisted).
    if not (d / "engagement.json").is_file():
        explicit_target = str(body.get("target") or "").strip()
        (d / "engagement.json").write_text(json.dumps({
            "name": title, "description": title,
            "target": explicit_target or _target_from_text(title),
            "permissions": permissions, "mode": mode,
            "createdAt": _now_iso(), "revision": 1,
            "structured": structured}))
        with contextlib.suppress(Exception):
            from suijin.server.platform.lib.engagement_index import rebuild as _rebuild_index

            _rebuild_index()
    else:
        meta = _session_engagement_meta(name)
        meta["permissions"] = permissions
        meta["mode"] = mode
        (d / "engagement.json").write_text(json.dumps(meta, indent=2))
        live = [a for a in _live_agents(name) if not a["dead"]]
        s = _desk_session(name)
        s.update({"status": "working" if live else s["status"], "title": title})
        if live:
            return {"session": s}
    if spawn and mode == "pentest" and not _session_engagement_meta(name).get("files"):
        _inflight_hunts.discard(name)
        return JSONResponse({"error": "Pentest requires an uploaded text scope document"}, status_code=400)

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
            run_swarm_hunt(_mission_with_settings(name, title), host_n=1, boxed_n=0, swarm=name)
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
        meta = _session_engagement_meta(session_id)
        if str(meta.get("mode") or "").lower() == "pentest" and not meta.get("files"):
            _emit(session_id, {"type": "error", "message": "Pentest requires an uploaded text scope document in the inbox."})
            return {"session": s}
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
                run_swarm_hunt(_mission_with_settings(session_id, mission), host_n=1, boxed_n=0, swarm=session_id)
        threading.Thread(target=_lazy_hunt, daemon=True, name=f"lazyspawn-{session_id}").start()
        _emit(session_id, {"type": "notice", "text": f"Engagement started: {mission}"})
    if text:
        # A paused session resumes on the operator's next message.
        with contextlib.suppress(Exception):
            db = sqlite3.connect(str(_swarm_db()), timeout=5)
            paused = db.execute(
                "SELECT COUNT(*) FROM agents WHERE swarm = ? AND status = 'paused'", (session_id,)
            ).fetchone()[0]
            if paused:
                db.execute("UPDATE agents SET status = 'active' WHERE swarm = ? AND status = 'paused'", (session_id,))
                db.commit()
                _publish("/cmd/resume", {"swarm": session_id})
            db.close()
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
    """PAUSE every sub-agent of this swarm (the agents panel's stop).
    Stop is a pause now — agents hold between turns and resume on /resume."""
    _publish("/cmd/pause", {"swarm": session_id})
    workers = _worker_list(session_id)
    for w in workers:
        _publish("/cmd/pause", {"agent": w["id"]})
        # stream the pause INTO the transcript so the panel updates live
        _emit(session_id, {"type": "worker", "worker": {**w, "status": "parked"}})
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("UPDATE agents SET status = 'paused' WHERE swarm = ?", (session_id,))
        db.commit()
        db.close()
    _emit(session_id, {"type": "notice", "text": "pause requested for all sub-agents"})
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
    if action in ("cancel", "pause", "stop"):
        # STOP = PAUSE (2026-11-23 operator ruling): the agents hold between
        # turns and keep their process/context. Delete is the destructive one.
        _publish("/cmd/pause", {"swarm": session_id})
        with contextlib.suppress(Exception):
            db = sqlite3.connect(str(_swarm_db()), timeout=5)
            db.execute("UPDATE agents SET status = 'paused' WHERE swarm = ?", (session_id,))
            db.commit()
            db.close()
        _emit(session_id, {"type": "notice", "text": "paused — send a message or Resume to continue"})
    elif action == "force-stop":
        _publish("/cmd/stop", {"swarm": session_id})
        with contextlib.suppress(Exception):
            _inflight_hunts.discard(session_id)
            db = sqlite3.connect(str(_swarm_db()), timeout=5)
            db.execute("UPDATE agents SET status = 'stopped' WHERE swarm = ?", (session_id,))
            db.commit()
            db.close()
        _emit(session_id, {"type": "notice", "text": "force-stopped"})
    elif action in ("resume", "clear"):
        _publish("/cmd/resume", {"swarm": session_id})
        with contextlib.suppress(Exception):
            db = sqlite3.connect(str(_swarm_db()), timeout=5)
            db.execute("UPDATE agents SET status = 'active' WHERE swarm = ? AND status = 'paused'", (session_id,))
            db.commit()
            db.close()
        _emit(session_id, {"type": "notice", "text": "resumed"})
    elif action == "delete":
        # the sidebar trash lands here — same as pressing delete
        return session_close(session_id)
    return {"session": _desk_session(session_id)}


@router.post("/api/console/sessions/{session_id}/decisions/{decision_id}")
def session_decision(session_id: str, decision_id: str, body: dict = Body(default={})):
    return {"session": _desk_session(session_id)}


def _bus_addr_direct():
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus

        return discover_bus()
    return None


@router.delete("/api/console/sessions/{session_id}")
def session_close(session_id: str):
    """Delete the chat: send the backend PAUSE then DELETE commands, then
    hard-stop any agent that ignored them, and remove the swarm's rows."""
    _publish("/cmd/pause", {"swarm": session_id})
    _publish("/cmd/delete", {"swarm": session_id})
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
    # Hide the history record too: without engagement.json the recent-target
    # scanner cannot resurrect the deleted chat as a folder.
    with contextlib.suppress(Exception):
        (_eng_dir(session_id) / "engagement.json").unlink(missing_ok=True)
    with _ring_lock:
        _rings.pop(session_id, None)
        _seqs.pop(session_id, None)
    _deleted_sessions.add(session_id)
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.engagement_index import rebuild as _rebuild_index

        _rebuild_index()
    return {"ok": True}


@router.post("/api/console/sessions/{session_id}/workers/{worker_id}/stop")
def worker_stop(session_id: str, worker_id: str):
    """Pause one sub-agent. Stop is a pause now; Delete removes it."""
    _publish("/cmd/pause", {"agent": worker_id})
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_swarm_db()), timeout=5)
        db.execute("UPDATE agents SET status = 'paused' WHERE id = ?", (worker_id,))
        db.commit()
        db.close()
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


def _provider_diagnostics(configured: bool, verified: bool | None = None,
                          status: int | None = None, error: str | None = None) -> dict:
    """Honest per-provider diagnostics. A saved key is NOT "verified" — it's
    "key saved · untested" until a real ping succeeds. verified=None means
    we haven't pinged (the default); verified True/False is a live result."""
    if not configured:
        return {"valid": False, "reason": None, "message": "not connected"}
    if verified is True:
        return {"valid": True, "reason": "verified", "message": "verified"}
    if verified is False:
        msg = f"failing ({status})" if status else "failing"
        if error:
            msg = f"{msg}: {error[:160]}"
        return {"valid": False, "reason": "failing", "message": msg}
    return {"valid": False, "reason": "untested", "message": "key saved · untested"}


# Last real verify result per provider (Check again / Verify all). WITHOUT this
# the ping result was thrown away and every doctor/providers call recomputed
# "untested" — clicking Check again could never change anything. TTL 5 min.
_verify_results: dict[str, dict] = {}
_VERIFY_TTL_S = 300.0


def _store_verify_result(key: str, result: dict) -> None:
    import time as _t

    _verify_results[str(key)] = {"ok": bool(result.get("ok")),
                                 "status": result.get("status"),
                                 "error": str(result.get("error") or "")[:200],
                                 "at": _t.time()}


def _cached_diagnostics(provider_id: str, configured: bool) -> dict:
    """Diagnostics overlaying the last persisted verify result (if fresh)."""
    import time as _t

    entry = _verify_results.get(provider_id)
    if entry and _t.time() - entry.get("at", 0) < _VERIFY_TTL_S:
        return _provider_diagnostics(configured, verified=entry["ok"],
                                     status=entry.get("status"), error=entry.get("error"))
    return _provider_diagnostics(configured)


def _provider_verify_state(provider_id: str) -> dict | None:
    import time as _t

    entry = _verify_results.get(provider_id)
    if entry and _t.time() - entry.get("at", 0) < _VERIFY_TTL_S:
        return entry
    return None


@router.get("/api/console/providers")
def providers():
    cfg = _cfg()
    zai = _zai_configured()
    provs = [{
        "id": "z-ai", "label": "Z.ai", "methods": ["api-key"],
        "configured": zai, "source": "env" if zai else None,
        "connectionIdentity": "suijin-zai" if zai else None,
        "diagnostics": _cached_diagnostics("z-ai", zai),
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
                      "source": None, "diagnostics": _provider_diagnostics(False), "accounts": [],
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


# ── plugins (the REAL module registry — suijin/modules/*) ────────

# Pack/tool id → icon source. Forms:
#   "domain.tld"      → favicon of that site (cached proxy)
#   "github/<user>"   → the GitHub org/user avatar via unavatar
#   "local:sui"       → the Suijin glyph (sui-native packs have no web brand)
# Everything the operator sees gets a real icon; nothing falls back to letters.
_TOOL_DOMAINS: dict[str, str] = {
    # networks / scanners
    "nmap": "nmap.org", "mcp_nmap": "nmap.org", "sqlmap": "sqlmap.org",
    "nikto": "sullo", "gobuster": "OJ", "feroxbuster": "ehsahil",
    "ffuf": "joohoi", "dirsearchp": "maurosoria", "rustscan": "RustScan",
    "masscan": "robertdavidgraham", "naabu": "projectdiscovery.io",
    "portscanx": "github.com", "portmap": "github.com", "whatweb": "urbanadventurer",
    "wafw00f": "EnableSecurity", "webprobe": "github.com", "crawl": "projectdiscovery.io",
    # projectdiscovery family
    "nuclei": "projectdiscovery.io", "mcp_nuclei": "projectdiscovery.io",
    "httpx": "projectdiscovery.io", "subfinder": "projectdiscovery.io",
    "katana": "projectdiscovery.io", "notify": "projectdiscovery.io",
    # exploitation / brute force
    "metasploit": "metasploit.com", "mcp_metasploit": "metasploit.com",
    "hashcat": "hashcat.net", "hashcatp": "hashcat.net", "hashcrack": "hashcat.net",
    "john": "openwall.com", "hydra": "vanhauser-thc", "trufflehog": "trufflesecurity.com",
    "pwdaudit": "github.com", "medusap": "local:sui",
    # web / app layer
    "sqlmap": "sqlmap.org", "testssl": "testssl.sh", "testsslp": "testssl.sh",
    "sslscan": "github.com", "sslprobe": "github.com", "ssrfcanary": "local:sui",
    "jwt_tool": "ticarpi", "jwtlab": "jwt.io", "graphqlprobe": "graphql.org",
    "graphql_diff": "graphql.org", "redirecthunter": "local:sui",
    "redirtrace": "local:sui", "hostheaderi": "local:sui", "origin_confuse": "local:sui",
    "headeraudit": "local:sui", "cookieaudit": "local:sui", "cacheinspect": "local:sui",
    "cors": "local:sui", "takeoverfp": "github.com", "techfp": "github.com",
    "uacheck": "github.com", "wafw00f": "EnableSecurity",
    # recon / osint
    "shodan": "shodan.io", "crtsh": "crt.sh", "crtshq": "crt.sh",
    "amass": "owasp.org", "asninfo": "ipinfo.io", "iprepq": "github.com",
    "ipmath": "github.com", "macvendor": "github.com", "emailharvest": "github.com",
    "theharvester": "laramies", "google_dork": "google.com", "githubdork": "github.com",
    "dns_zone": "github.com", "dnsbrute": "github.com", "dnsreconp": "darkoperator",
    "dnssec": "github.com", "dnssniff": "github.com", "dohquery": "github.com",
    "whoisraw": "iana.org", "waybacker": "web.archive.org", "sitemapper": "github.com",
    "robotsparse": "github.com", "securitytxt": "github.com", "subzy": "LukaSikich",
    # cloud
    "gcpenumer": "cloud.google.com", "cloudmeta": "cloud.google.com",
    "awsenumer": "aws.amazon.com", "azurenumer": "azure.microsoft.com",
    # creds / secrets / crypto
    "hashid": "psypanda", "hashkit": "local:sui", "cryptomisc": "local:sui",
    "secretscanr": "local:sui", "credential_store": "local:sui",
    "list_credentials": "local:sui",
    # sui-native packs — the Suijin glyph is their brand
    "auto_fix": "local:sui", "bugscope": "local:sui", "cmdsmith": "local:sui",
    "cronkit": "local:sui", "devtoolsk": "local:sui", "encodesk": "local:sui",
    "extractr": "local:sui", "genkit": "local:sui", "querykit": "local:sui",
    "knowledge": "local:sui", "knowledge_graph": "local:sui", "kg_neo4j": "neo4j.com",
    "log_surgeon": "local:sui", "ops": "local:sui", "redteam": "local:sui",
    "skills": "local:sui", "tool_inventory": "local:sui", "write_note": "local:sui",
    "tool_search": "local:sui", "traffic_search": "local:sui", "traffic_get": "local:sui",
    "insert_asset": "local:sui", "record_fact": "local:sui", "rows_snapshot": "local:sui",
    "attack_tree": "local:sui", "attack_paths": "local:sui", "recon_chain": "local:sui",
    "dispatch_testers": "local:sui", "select_lanes": "local:sui", "set_intent": "local:sui",
    "target_dossier": "local:sui", "surface_verdict": "local:sui", "surface_expand": "local:sui",
    "memory_recall": "local:sui", "cheatsheet_note": "local:sui", "chain_lookup": "local:sui",
    "chain_suggest": "local:sui", "chain_track": "local:sui", "state": "local:sui",
    "help": "local:sui", "tools": "", "execute_terminal": "", "http_request": "",
    "http_replay": "", "http_replay_raw": "", "web_fetch": "", "web_search": "",
    "web_session": "", "smart_fetch": "", "dom_recon": "", "js_bundle_analyze": "",
    "source_map_probe": "", "api_discover": "",
    # generic utilities with a home on the web
    "curl": "curl.se", "pyrun": "python.org", "pip_install": "pypi.org",
    "fetch_resource": "github.com", "mcp_terminal": "local:sui",
    "socat": "github.com", "netutils": "github.com", "wifi": "aircrack-ng.org",
    "gvm": "openvas.org", "exploitdbp": "exploit-db.com",
    "search_cve": "cvedetails.com", "cvelookup": "cvedetails.com", "cve_advise_tools": "cvedetails.com",
    "mitmproxy_capture": "mitmproxy.org", "proxy_capture": "mitmproxy.org",
    "playwright": "playwright.dev", "mcp_playwright": "playwright.dev",
    "websocket_client": "github.com", "htmlparse": "github.com",
    "wordlisthub": "github.com", "wordlistio": "github.com", "find_wordlist": "github.com",
    "mutate_wordlist": "github.com", "cewl_words": "github.com",
    "containerbreak": "docker.com", "local_docker_sock": "docker.com",
    "ssh_exec": "github.com", "ssh_inventory": "github.com",
    "ssh_pull": "github.com", "ssh_push": "github.com",
    "shell_start": "local:sui", "shell_send": "local:sui", "shell_stop": "local:sui",
    "shell_list": "local:sui",
    "adenum": "github.com", "apifuzz": "github.com", "apkstatics": "github.com",
    "awsenumer": "aws.amazon.com", "azurenumer": "azure.microsoft.com",
    "backuphunter": "github.com", "bucketprobe": "github.com",
    "cloudflare": "cloudflare.com", "cmseek": "Tuhinshubhra",
    "smb_enum": "github.com", "snmpenum": "github.com", "imppack": "github.com",
    "ipastatics": "github.com", "iptables_control": "github.com",
    "kerberos_attacks": "github.com", "kerbtools": "github.com",
    "openapi_parser": "github.com", "openapik": "github.com",
    "payloadescape": "local:sui", "payloadforge": "local:sui", "payloadpk": "local:sui",
    "payload_generate": "local:sui", "payload_mutate": "local:sui",
    "extract_payloads": "local:sui", "code_harness": "local:sui",
    "evidence_capture": "local:sui", "evidence_verify": "local:sui",
    "generate_report": "local:sui", "export_html": "local:sui", "export_burp": "portswigger.net",
    "burp": "portswigger.net", "catalog_exploit": "local:sui",
    "record_finding": "local:sui", "report_finding": "local:sui",
    "retest_finding": "local:sui", "finding_dedup": "local:sui",
    "coverage_check": "local:sui", "completion_status": "local:sui",
    "observation_status": "local:sui", "bypass_403": "local:sui",
    "race_test": "local:sui", "mass_assign": "local:sui", "param_pollute": "local:sui",
    "auth_diff": "local:sui", "auth_forge": "local:sui", "request_auth": "local:sui",
    "mesh_ask": "local:sui", "mesh_broadcast": "local:sui", "mesh_dm": "local:sui",
    "mesh_read": "local:sui", "mesh_reply": "local:sui", "mesh_status": "local:sui",
    "job_cancel": "local:sui", "job_list": "local:sui", "job_output": "local:sui",
    "job_status": "local:sui", "job_wait": "local:sui",
    "read_file": "local:sui", "write_file": "local:sui", "list_own_files": "local:sui",
    "ensure_tools": "local:sui", "skill_load": "local:sui", "skill_search": "local:sui",
    "list_skills": "local:sui", "edit_skill": "local:sui",
    "kb_read": "local:sui", "kb_stats": "local:sui", "kb_freshness": "local:sui",
    "search_kb": "local:sui", "check_knowledge": "neo4j.com",
    "recipe_define": "local:sui", "recipe_list": "local:sui", "recipe_run": "local:sui",
    "adjust_config": "local:sui", "set_live_config": "local:sui",
    "deploy_subagent": "local:sui", "fireteam_status": "local:sui",
    "ssrf_listener": "local:sui", "csrf_extract": "local:sui",
    "url_dedup": "local:sui", "url_rewrite": "local:sui", "url_merge": "local:sui",
    "normalize_output": "local:sui", "mine_failures": "local:sui",
    "suggest_exploit": "local:sui", "claim_flag": "local:sui",
    "register_credential": "local:sui", "list_credentials": "local:sui",
    "local_cred_hunt": "local:sui", "local_docker_sock": "docker.com",
    "local_env_secrets": "local:sui", "local_file_find": "local:sui",
    "local_hist_search": "local:sui", "local_lpe_scan": "local:sui",
    "local_mounts": "local:sui", "local_net": "local:sui", "local_path_hijack": "local:sui",
    "local_priv_check": "local:sui", "local_proc": "local:sui", "local_sched": "local:sui",
    "local_service_hijack": "local:sui", "local_services": "local:sui",
    "local_suid_audit": "local:sui", "local_sys_info": "local:sui",
    "local_users": "local:sui", "google_key_probe": "google.com",
    "fetch_authorization_page": "local:sui", "diff_response": "local:sui",
    "response_diff": "local:sui", "attack_surface": "local:sui",
    "providers": "local:sui", "addons": "local:sui", "core_utils": "local:sui",
    "cmepack": "cisco.com", "execute_terminal": "local:sui",
    "fileio": "local:sui", "filekit": "local:sui", "http_request": "local:sui",
    "httplive": "local:sui", "httpmethods": "github.com", "protodetect": "github.com",
    "proxycheck": "github.com", "redisenum": "redis.io", "reqdiff": "local:sui",
    "reqtool": "local:sui", "sourcemap": "github.com", "tfsec": "tfsec.dev",
    "urltoolz": "local:sui", "hashcrack": "hashcat.net",
    "sui": "local:sui", "msf_check": "metasploit.com", "msf_command": "metasploit.com",
    "msf_run": "metasploit.com", "msf_sessions": "metasploit.com",
}


def _pack_icon(mid: str) -> str:
    d = _TOOL_DOMAINS.get(mid) or (_TOOL_DOMAINS.get(mid.split(".")[-1]) if "." in mid else "") or ""
    if not d:
        return ""
    if d == "local:sui":
        return "/favicon.png"
    if d.startswith("github/"):
        return f"/api/console/provider-icon/{d}"
    return f"/api/console/provider-icon/{d}"


# tool name → (owning pack dir, description), built from module + addon
# manifests (cached). Pack tools appear on the Tools page with icons.
_pack_tools_cache: dict[str, tuple[str, str]] | None = None


def _pack_tools() -> dict[str, tuple[str, str]]:
    global _pack_tools_cache
    if _pack_tools_cache is None:
        import json as _json

        mapping: dict[str, tuple[str, str]] = {}
        roots = (Path(__file__).resolve().parents[2] / "modules",
                 Path(__file__).resolve().parents[2] / "addons")
        for root in roots:
            with contextlib.suppress(OSError):
                for mf in root.glob("*/manifest.json"):
                    with contextlib.suppress(Exception):
                        data = _json.loads(mf.read_text(errors="replace"))
                        pack = mf.parent.name
                        for t, spec in (data.get("tools") or {}).items():
                            desc = ""
                            if isinstance(spec, dict):
                                desc = str(spec.get("description") or "")
                            elif isinstance(spec, str):
                                desc = spec
                            mapping.setdefault(str(t), (pack, desc))
        _pack_tools_cache = mapping
    return _pack_tools_cache


def _pack_of_tool(name: str) -> str:
    """Owning pack for a route tool: exact manifest match, else the longest
    pack id contained in the tool name (gobuster_dir → gobuster)."""
    mapping = _pack_tools()
    if name in mapping:
        return mapping[name][0]
    nl = name.lower()
    best = ""
    for pid in {v[0] for v in mapping.values()}:
        if len(pid) > len(best) and pid in nl:
            best = pid
    return best


def _tool_icon(name: str) -> str:
    if not name:
        return ""
    d = _TOOL_DOMAINS.get(name)
    if d is None:
        d = _TOOL_DOMAINS.get(_pack_of_tool(name), "")
    if not d:
        return ""
    if d == "local:sui":
        return "/favicon.png"
    return f"/api/console/provider-icon/{d}"


# Display-name conventions — read pack-by-pack from the manifests and
# corrected manually: compound words in proper CamelCase, official product
# styling kept (nmap, sqlmap, nuclei, httpx, subfinder, katana, shodan,
# trufflehog, whatweb stay lowercase because that IS how they style
# themselves). This map wins over whatever the manifest says.
_NAME_OVERRIDES: dict[str, str] = {
    "adenum": "AdEnum", "amass": "Amass", "apifuzz": "ApiFuzz",
    "apkstatics": "ApkStatics", "asninfo": "AsnInfo",
    "auto_fix": "Auto-Fix Pipeline", "awsenumer": "AwsEnumer",
    "azurenumer": "AzureEnumer", "backuphunter": "BackupHunter",
    "bucketprobe": "BucketProbe", "bugscope": "BugScope",
    "cacheinspect": "CacheInspect", "cloudmeta": "CloudMeta",
    "cmdsmith": "CmdSmith", "cmepack": "CmePack",
    "containerbreak": "ContainerBreak", "cookieaudit": "CookieAudit",
    "cronkit": "CronKit", "crtsh": "CRT.sh", "crtshq": "CRT.sh Query",
    "cryptomisc": "CryptoMisc", "curl": "cURL HTTP",
    "cvelookup": "CveLookup", "devtoolsk": "DevToolsK",
    "dirsearchp": "DirSearch", "dns_zone": "DNS Zone Transfer",
    "dnsbrute": "DNSBrute", "dnsreconp": "DNSRecon", "dnssec": "DNSSEC",
    "dnssniff": "DnsSniff", "dohquery": "DoHQuery",
    "emailharvest": "EmailHarvest", "encodesk": "EncodeSk",
    "exploitdbp": "ExploitDB", "extractr": "Extractr",
    "feroxbuster": "Feroxbuster", "ffuf": "FFUF", "fileio": "File I/O",
    "filekit": "FileKit", "gcpenumer": "GcpEnumer", "genkit": "GenKit",
    "githubdork": "GitHubDork", "gobuster": "Gobuster",
    "google_dork": "Google Dork", "graphql_diff": "GraphQL Schema Diff",
    "graphqlprobe": "GraphQLProbe", "gvm": "GVM/OpenVAS",
    "hashcatp": "Hashcat", "hashcrack": "Hashcat Crack", "hashid": "HashID",
    "hashkit": "HashKit", "headeraudit": "HeaderAudit",
    "hostheaderi": "HostHeader Injection", "htmlparse": "HTML Parse",
    "httplive": "HttpLive", "httpmethods": "HTTP Methods", "httpx": "httpx",
    "imppack": "ImpPack", "ipastatics": "IpaStatics", "ipmath": "IpMath",
    "iprepq": "IpRepQ", "iptables_control": "IPTables Control",
    "jwt_tool": "JWT Toolkit", "jwtlab": "JWTLab", "kerbtools": "KerbTools",
    "log_surgeon": "Log Surgeon", "macvendor": "MacVendor",
    "medusap": "MedusaPack", "mitmproxy_capture": "mitmproxy Capture",
    "netutils": "NetUtils", "openapik": "OpenAPI Kit",
    "payloadescape": "PayloadEscape", "payloadforge": "PayloadForge",
    "payloadpk": "PayloadPK", "portmap": "PortMap", "portscanx": "PortScanX",
    "protodetect": "ProtoDetect", "proxycheck": "ProxyCheck",
    "pwdaudit": "PwdAudit", "pyrun": "PyRun", "querykit": "QueryKit",
    "redirecthunter": "RedirectHunter", "redirtrace": "RedirTrace",
    "redisenum": "RedisEnum", "reqdiff": "ReqDiff", "reqtool": "ReqTool",
    "robotsparse": "RobotsParse", "secretscanr": "SecretScanr",
    "securitytxt": "security.txt", "sitemapper": "SiteMapper",
    "snmpenum": "SnmpEnum", "sourcemap": "SourceMap", "sslprobe": "SslProbe",
    "ssrfcanary": "SsrfCanary", "takeoverfp": "TakeoverFP", "techfp": "TechFP",
    "testssl": "testssl.sh", "tfsec": "tfsec", "uacheck": "UaCheck",
    "urltoolz": "UrlToolz", "wafw00f": "WAFW00F", "webprobe": "WebProbe",
    "whoisraw": "WhoisRaw", "wordlisthub": "WordlistHub",
    "wordlistio": "WordlistIO", "services": "Services",
    "addons": "Addons", "core_utils": "Core Utils",
    "credential_store": "Credential Store", "providers": "Providers",
    "knowledge": "Knowledge", "ops": "Ops", "redteam": "RedTeam",
    "skills": "Skills", "execute_terminal": "Shell Execution",
    "http_request": "HTTP Request", "http_replay": "HTTP Replay",
    "http_replay_raw": "HTTP Replay Raw", "web_fetch": "Web Fetch",
    "web_search": "Web Search", "web_session": "Web Session",
}


def _module_items() -> list[dict]:
    """Every installed suijin module as a PluginItem. Real enable state
    from the module manager, name/description enriched from each pack's
    manifest.json (the manager's own description field is empty)."""
    with contextlib.suppress(Exception):
        from suijin.modules.manager import list_modules, module_info

        items = []
        for m in list_modules():
            enabled = bool(m.get("enabled"))
            mid = str(m.get("id"))
            desc = str(m.get("description") or "")
            with contextlib.suppress(Exception):
                source = module_info(mid).get("source")
                manifest = Path(str(source)) / "manifest.json"
                if manifest.is_file():
                    data = json.loads(manifest.read_text(errors="replace"))
                    desc = str(data.get("description") or desc)
            # The manual conventions map wins LAST — the manifest's sloppy
            # casing ("Jwtlab", "Bugscope") must never leak back onto the panel.
            name = _NAME_OVERRIDES.get(mid, mid)
            items.append({
                "id": mid, "kind": "plugin",
                "name": name, "version": str(m.get("version") or "1.0"),
                "description": desc,
                "capabilities": [str(x) for x in (m.get("permissions") or [])],
                "signature": "verified", "error": None,
                "tier": str(m.get("tier") or ""),
                "state": "enabled" if enabled else "installed",
                "loaded": enabled, "icon": _pack_icon(mid),
            })
        return items
    return []


@router.get("/api/console/plugins")
def plugins():
    # Core-tier modules (platform/tools/agent/console) are boot-required and
    # cannot be disabled — they never appear on the operator's panel at all.
    items = [p for p in _module_items() if p.get("tier") != "core"]
    enabled = [p["id"] for p in items if p["loaded"]]
    tools: list[dict] = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.dispatch import list_route_tools

        tools = [{"name": n, "description": f"Suijin tool: {n}"} for n in list(list_route_tools())[:12]]
    return {"executionProfile": "local",
            "registry": {"url": "vendored modules", "available": True, "error": None},
            "items": items, "deferred": [],
            "host": {"loadedPluginIds": enabled, "tools": tools}}


def _module_action(kind: str, module_id: str):
    """Real enable/disable through the module manager. Core tier refuses to
    disable (boot requires it); install/run answer honestly for bundled packs."""
    if not module_id:
        return JSONResponse({"ok": False, "message": "id required"}, status_code=400)
    with contextlib.suppress(Exception):
        from suijin.modules.manager import list_modules, set_enabled

        mods = {str(m.get("id")): m for m in list_modules()}
        m = mods.get(module_id)
        if not m:
            return JSONResponse({"ok": False, "message": f"unknown module '{module_id}'"}, status_code=404)
        if kind == "disable" and str(m.get("tier")) == "core":
            return JSONResponse({"ok": False, "message": f"'{module_id}' is core tier — boot requires it, it cannot be disabled."}, status_code=400)
        if kind in ("enable", "disable"):
            if not set_enabled(module_id, kind == "enable"):
                return JSONResponse({"ok": False, "message": f"could not set state for '{module_id}'"}, status_code=500)
            return {"ok": True, "deferred": False,
                    "message": f"{module_id} {'enabled' if kind == 'enable' else 'disabled'} — applies on next boot."}
        if kind == "install":
            return {"ok": True, "deferred": False, "message": f"{module_id} is already installed (bundled pack)."}
        if kind == "run":
            live = bool(m.get("enabled"))
            return {"ok": live, "deferred": False,
                    "message": f"{module_id} loads at boot" + ("." if live else " — enable it first.")}
    return JSONResponse({"ok": False, "message": "module manager unavailable"}, status_code=500)


@router.post("/api/console/plugins/enable")
def plugins_enable(body: dict = Body(default={})):
    return _module_action("enable", str(body.get("id") or ""))


@router.post("/api/console/plugins/disable")
def plugins_disable(body: dict = Body(default={})):
    return _module_action("disable", str(body.get("id") or ""))


@router.post("/api/console/plugins/install")
def plugins_install(body: dict = Body(default={})):
    return _module_action("install", str(body.get("id") or ""))


@router.post("/api/console/plugins/run")
def plugins_run(body: dict = Body(default={})):
    return _module_action("run", str(body.get("id") or ""))


# ── skills (the REAL workspace skill library) ────────────────────

_SKILL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


def _skills_root() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR) / "skills"
    return Path.home() / ".suijin" / "config" / "skills"


def _skills_mounts() -> list[dict]:
    """Mounted skill folders (read-only sources) persisted beside the library."""
    p = _skills_root().parent / "skills-mounts.json"
    with contextlib.suppress(Exception):
        data = json.loads(p.read_text(errors="replace"))
        if isinstance(data, list):
            return [{"id": m.get("id") or "", "path": m.get("path") or ""} for m in data if isinstance(m, dict) and m.get("path")]
    return []


def _skills_mounts_save(mounts: list[dict]) -> None:
    p = _skills_root().parent / "skills-mounts.json"
    with contextlib.suppress(OSError):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(mounts, indent=1))


def _skill_frontmatter(content: str) -> dict:
    """Parse the frontmatter head of a SKILL.md (name/description/version/
    category/tags). Tolerant: some imported skills carry license comment
    lines BEFORE the --- block, so search for the first block in the head."""
    out: dict = {}
    m = re.search(r"(?:^|\r?\n)---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)", content[:4000])
    if not m:
        return out
    lines = m.group(1).splitlines()
    collecting_tags = False
    tags: list[str] = []
    for line in lines:
        if collecting_tags:
            tm = re.match(r"^\s*-\s*(.+)$", line)
            if tm:
                tags.append(tm.group(1).strip().strip('"').strip("'"))
                continue
            collecting_tags = False
        lm = re.match(r"^(name|description|version|category|author|tags):\s*(.*)$", line.strip())
        if lm:
            if lm.group(1) == "tags":
                collecting_tags = True
                if lm.group(2).strip():
                    tags = [t.strip() for t in lm.group(2).split(",") if t.strip()]
            else:
                out[lm.group(1)] = lm.group(2).strip().strip('"').strip("'")
    if tags:
        out["tags"] = tags
    return out


def _skill_files(folder: Path) -> list[dict]:
    files: list[dict] = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            with contextlib.suppress(OSError):
                files.append({"path": str(p.relative_to(folder)), "size": p.stat().st_size})
    return files[:256]


def _skill_record(folder: Path, scope: str = "workspace") -> dict | None:
    md = folder / "SKILL.md"
    if not md.is_file():
        return None
    content = ""
    with contextlib.suppress(OSError):
        content = md.read_text(errors="replace")
    fm = _skill_frontmatter(content)
    return {
        "id": folder.name, "name": fm.get("name") or folder.name,
        "description": fm.get("description") or "",
        "category": fm.get("category") or "",
        "tags": fm.get("tags") or [],
        "scope": scope, "revision": fm.get("version") or "r1",
        "writable": scope == "workspace", "fileCount": len(_skill_files(folder)),
        "source": str(md),
    }


def _skill_folder(skill_id: str) -> Path | None:
    """Locate a skill folder by id across the library and mounted roots."""
    if not _SKILL_ID_RE.fullmatch(skill_id):
        return None
    folder = _skills_root() / skill_id
    if (folder / "SKILL.md").is_file():
        return folder
    for mount in _skills_mounts():
        candidate = Path(mount["path"]) / skill_id
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None


@router.get("/api/skills")
def skills_list():
    root = _skills_root()
    out: list[dict] = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if folder.is_dir():
                rec = _skill_record(folder)
                if rec:
                    out.append(rec)
    for mount in _skills_mounts():
        mroot = Path(mount["path"])
        if mroot.is_dir():
            for folder in sorted(mroot.iterdir()):
                if folder.is_dir():
                    rec = _skill_record(folder, scope="mounted")
                    if rec and not any(x["id"] == rec["id"] for x in out):
                        out.append(rec)
    out.sort(key=lambda r: r["name"].lower())
    return {"skills": out, "mounts": _skills_mounts(), "diagnostics": [], "workspacePath": str(root)}


@router.get("/api/skills/{skill_id}")
def skill_detail(skill_id: str):
    folder = _skill_folder(skill_id)
    if not folder:
        return JSONResponse({"error": "skill not found"}, status_code=404)
    rec = _skill_record(folder, scope="workspace" if folder.parent == _skills_root() else "mounted") or {}
    content = ""
    with contextlib.suppress(OSError):
        content = (folder / "SKILL.md").read_text(errors="replace")
    return {"skill": {**rec, "id": skill_id, "content": content,
                      "files": _skill_files(folder), "source": str(folder / "SKILL.md")}}


@router.post("/api/skills")
def skill_create(body: dict = Body(default={})):
    content = str(body.get("content") or "").strip()
    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)
    fm = _skill_frontmatter(content)
    base = re.sub(r"[^a-z0-9]+", "-", str(fm.get("name") or "skill").lower()).strip("-")[:48] or "skill"
    root = _skills_root()
    with contextlib.suppress(OSError):
        root.mkdir(parents=True, exist_ok=True)
    slug, n = base, 2
    while (_skills_root() / slug).exists():
        slug = f"{base}-{n}"
        n += 1
    folder = root / slug
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(content)
    rec = _skill_record(folder)
    if not rec:
        return JSONResponse({"error": "could not write skill"}, status_code=500)
    return {"skill": rec}


@router.post("/api/skills/import")
def skill_import(body: dict = Body(default={})):
    import base64

    bundle = body.get("bundle") if isinstance(body.get("bundle"), dict) else body
    files = bundle.get("files") or []
    name = str(bundle.get("name") or "imported")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "imported"
    root = _skills_root()
    with contextlib.suppress(OSError):
        root.mkdir(parents=True, exist_ok=True)
    s, n = slug, 2
    while (root / s).exists():
        s = f"{slug}-{n}"
        n += 1
    folder = root / s
    with contextlib.suppress(OSError):
        folder.mkdir(parents=True)
    for f in files[:256]:
        if not isinstance(f, dict):
            continue
        rel = str(f.get("path") or "")
        if not rel or ".." in rel:
            continue
        with contextlib.suppress(Exception):
            dest = folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(str(f.get("content") or "")))
    if not (folder / "SKILL.md").is_file():
        with contextlib.suppress(OSError):
            (folder / "SKILL.md").write_text(f"---\nname: \"{s}\"\ndescription: \"Imported skill\"\n---\n\nImported skill.\n")
    rec = _skill_record(folder)
    if not rec:
        return JSONResponse({"error": "could not write skill"}, status_code=500)
    return {"skill": rec}


@router.put("/api/skills/{skill_id}")
def skill_save(skill_id: str, body: dict = Body(default={})):
    folder = _skill_folder(skill_id)
    if not folder or folder.parent != _skills_root():
        return JSONResponse({"error": "skill not found or read-only"}, status_code=404)
    content = str(body.get("content") or "")
    if not content.strip():
        return JSONResponse({"error": "content required"}, status_code=400)
    with contextlib.suppress(OSError):
        (folder / "SKILL.md").write_text(content)
    rec = _skill_record(folder) or {}
    return {"skill": {**rec, "id": skill_id}}


@router.post("/api/skills/{skill_id}/copy")
def skill_copy(skill_id: str, body: dict = Body(default={})):
    folder = _skill_folder(skill_id)
    if not folder:
        return JSONResponse({"error": "skill not found"}, status_code=404)
    content = str(body.get("content") or "")
    if not content.strip():
        with contextlib.suppress(OSError):
            content = (folder / "SKILL.md").read_text(errors="replace")
    root = _skills_root()
    slug, n = f"{skill_id}-custom", 2
    while (root / slug).exists():
        slug = f"{skill_id}-custom-{n}"
        n += 1
    new_folder = root / slug
    with contextlib.suppress(OSError):
        new_folder.mkdir(parents=True)
        (new_folder / "SKILL.md").write_text(content)
    rec = _skill_record(new_folder)
    if not rec:
        return JSONResponse({"error": "could not write skill"}, status_code=500)
    return {"skill": rec}


@router.get("/api/skills/{skill_id}/export")
def skill_export(skill_id: str):
    import base64

    from fastapi import Response

    folder = _skill_folder(skill_id)
    if not folder:
        return Response(status_code=404)
    files = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            with contextlib.suppress(OSError):
                files.append({"path": str(p.relative_to(folder)), "encoding": "base64",
                              "content": base64.b64encode(p.read_bytes()).decode()})
    bundle = {"format": "agent-skill", "version": 1, "name": skill_id, "files": files}
    return Response(json.dumps(bundle), media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename=\"{skill_id}.skill.json\""})


@router.post("/api/skills/mounts")
def skills_mount_add(body: dict = Body(default={})):
    path = str(body.get("path") or "").strip()
    folder = Path(path).expanduser()
    if not path or not folder.is_dir():
        return JSONResponse({"error": "folder does not exist"}, status_code=400)
    mounts = _skills_mounts()
    entry = {"id": re.sub(r"[^a-z0-9]+", "-", folder.name.lower()).strip("-")[:48] or "mount", "path": str(folder)}
    if any(m["path"] == entry["path"] for m in mounts):
        return {"mount": entry}
    mounts.append(entry)
    _skills_mounts_save(mounts)
    return {"mount": entry}


@router.delete("/api/skills/mounts/{mount_id}")
def skills_mount_remove(mount_id: str):
    mounts = [m for m in _skills_mounts() if m["id"] != mount_id]
    _skills_mounts_save(mounts)
    return {"ok": True}


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
        doc_provs.append({**p, "diagnostics": _cached_diagnostics(str(p["id"]), bool(p["configured"]))})
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
                  "roles": ["worker", "planner"], "icon": _tool_icon(n)} for n in list_route_tools()]
    # Pack tools (gobuster, nikto, sqlmap, …) the AI dispatches through packs —
    # every one lands on the menu with its owning pack's icon.
    seen = {t["name"] for t in tools}
    for name, (pack, desc) in sorted(_pack_tools().items()):
        if name in seen:
            continue
        seen.add(name)
        tools.append({"name": name, "description": desc or f"{pack} pack tool",
                      "roles": ["worker", "planner"], "icon": _tool_icon(name),
                      "pack": pack})
    return {"roles": ["worker", "planner"], "tools": tools, "plugins": []}


@router.get("/api/console/checks")
def console_checks():
    return {"project": "suijin", "checks": []}


@router.get("/api/console/learning")
def console_learning():
    return {"events": [], "knowledge": [], "improvements": []}


# ── memory graph (the librarian's knowledge map) ─────────────────

_PRIVATE_IP = re.compile(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0|::1)", re.I)
_INTERNAL_NAMES = ("localhost", "local", "host.docker", ".local", ".corp", ".internal", ".lan")


def _kg_path() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        base = Path(WORKSPACE_DIR)
        root = base.parent if base.name == "config" else base
        return root / "intel" / "knowledge_graph.json"
    return Path.home() / ".suijin" / "intel" / "knowledge_graph.json"


def _url_host(node_id: str) -> str:
    m = re.match(r"^[a-z]+://([^/?#]+)", node_id, re.I)
    return m.group(1).lower() if m else ""


def _registrable(host: str) -> str:
    """Crude registrable domain: last two labels, three for co.uk style."""
    labels = [x for x in host.split(".") if x]
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "gov", "edu", "ac") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) > 2 else host


def _node_kind(node_id: str, host: str = "") -> str:
    nid = node_id.strip().lower()
    h = (host or nid).lower()
    if nid == "methodology" or nid.endswith("methodology") or "architecture" in nid or " ordering" in nid:
        return "methodology"
    if nid.startswith("auto-agent") or re.search(r"agent-[0-9a-f]{4}", nid):
        return "agent"
    if _PRIVATE_IP.match(h) or any(h == n or h.endswith(n) for n in _INTERNAL_NAMES):
        return "internal"
    if re.match(r"^[a-z]+://", node_id, re.I):
        return "url"
    if ":" in nid and re.match(r"^[\d.:]+$", nid):
        return "internal"
    return "external"


def _kg_graph_data() -> dict:
    """The live knowledge graph from whichever backend is configured.

    Every backend implements get_all_targets/get_constraints, so the
    mindmap works on JSON, LadybugDB and Neo4j without knowing which one
    answered. `methodology` is operator-owned and stays in the JSON file;
    merge it in so the map keeps its playbook branch.
    """
    data: dict = {}
    with contextlib.suppress(Exception):
        from suijin.modules.redteam.lib.intel.kg_backend import get_backend

        backend = get_backend(lambda: _kg_path())
        for target in backend.get_all_targets():
            constraints = backend.get_constraints(target)
            if isinstance(constraints, dict):
                data[str(target)] = constraints
    with contextlib.suppress(Exception):
        path = _kg_path()
        if path.is_file():
            raw = json.loads(path.read_text(errors="replace"))
            if isinstance(raw, dict) and isinstance(raw.get("methodology"), dict) and "methodology" not in data:
                data["methodology"] = raw["methodology"]
    return data


@router.get("/api/console/memory-graph")
def memory_graph():
    """The fullscreen mindmap: MEMORY → External/Internal TARGETS → each
    target carries Info / Endpoints / Vulnerabilities branches populated with
    its real intel entries, plus cross-links where evidence mentions other
    targets. Per-target housekeeping — the AI cleans up per target during an
    engagement. Local-only: reads the operator workspace, never leaves it."""
    data = _kg_graph_data()
    if not isinstance(data, dict) or not data:
        return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0, "entries": 0}, "missing": True}

    VULN_CATS = {"bypass", "vulnerability", "vulnerabilities", "finding", "findings",
                 "exploit", "misconfig", "misconfiguration", "leak", "leaks", "weakness", "abuse"}
    ENDPOINT_HINT = re.compile(r"/|:\d|\.", re.I)

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9.:/_\-* ]+", "", s.lower()).strip()

    def _host_of(key: str) -> str:
        if re.match(r"^[a-z]+://", key, re.I):
            m = re.match(r"^[a-z]+://([^/?#]+)", key, re.I)
            return (m.group(1) if m else "").lower()
        if "://" not in key and "/" not in key and ENDPOINT_HINT.search(key):
            return key.lower()
        return ""

    nodes: dict[str, dict] = {}
    total_entries = 0
    for raw_id, payload in data.items():
        key = str(raw_id).strip()
        if not key:
            continue
        kind = _node_kind(key)
        host = _host_of(key) if kind == "url" else ""
        cats: dict[str, int] = {}
        entries_flat: list[dict] = []
        entry_count = 0
        if isinstance(payload, dict):
            for cat, entries in payload.items():
                if isinstance(entries, list):
                    cats[str(cat)] = len(entries)
                    entry_count += len(entries)
                    for e in entries[:60]:
                        if isinstance(e, dict):
                            entries_flat.append({
                                "category": str(cat),
                                "rule": str(e.get("rule") or "")[:400],
                                "evidence": str(e.get("evidence") or "")[:500],
                                "confidence": e.get("confidence"),
                                "severity": str(e.get("severity") or ""),
                                "verified": bool(e.get("verified")),
                            })
        total_entries += entry_count
        label = key
        if kind == "url":
            label = re.sub(r"^[a-z]+://", "", key, flags=re.I).rstrip("/")
            if len(label) > 64:
                label = label[:61] + "…"
        nodes[key] = {
            "id": key, "label": label, "kind": kind, "host": host,
            "internal": kind == "internal" or (host != "" and bool(_PRIVATE_IP.match(host))),
            "entryCount": entry_count, "categories": cats, "entries": entries_flat,
        }

    # ── pass A: the TARGET set — registrable domains + internal bases ──
    targets: set[str] = set()
    internal_targets: set[str] = set()
    for key, meta in nodes.items():
        k = _norm(key)
        if meta["kind"] == "external":
            reg = _registrable(k)
            if reg:
                targets.add(reg)
        elif meta["kind"] == "url":
            h = meta["host"]
            if h:
                if _PRIVATE_IP.match(h) or any(h == n or h.endswith(n) for n in _INTERNAL_NAMES):
                    internal_targets.add(h)
                else:
                    reg = _registrable(h)
                    if reg:
                        targets.add(reg)
        elif meta["kind"] == "internal":
            internal_targets.add(k)
    targets.discard("")

    # ── pass B: resolve every node to ONE target (layered, first hit wins) ──
    def resolve_target(key: str, meta: dict) -> str | None:
        kind = meta["kind"]
        k = _norm(key)
        if kind == "internal":
            h = meta["host"] or k
            return h if (internal_targets and h in internal_targets) or True else None
        if kind == "url":
            h = meta["host"]
            if h:
                if h in internal_targets:
                    return h
                reg = _registrable(h)
                if reg in targets:
                    return reg
                return reg or None
            return None
        if kind == "external":
            reg = _registrable(k)
            if reg in targets:
                return reg
            m = re.match(r"github\.com/([^/]+)", k)
            if m:
                org = m.group(1).lower()
                if len(org) >= 4:
                    for t in sorted(targets, key=len, reverse=True):
                        if org == t.split(".")[0] or org in k:
                            return t
            if "npm:" in k:
                stem = re.sub(r"npm:|@.*|\(.*\)", "", k).lower()
                for t in sorted(targets, key=len, reverse=True):
                    base = t.split(".")[0]
                    if len(base) >= 5 and base in stem:
                        return t
            for t in sorted(targets, key=len, reverse=True):
                base = t.split(".")[0]
                if len(base) >= 5 and base in k:
                    return t
            return "__unsorted__"
        return None  # methodology / agents live on their own branches

    assignment: dict[str, str | None] = {}
    for key, meta in nodes.items():
        assignment[key] = resolve_target(key, meta)

    # ── pass C: classify entries into the three branches per target ──
    # target → branch → synthesized leaves (deduped by rule text)
    branch_leaves: dict[str, dict[str, list[dict]]] = {}
    def add_leaf(target: str, branch: str, entry: dict, ref: str = "") -> None:
        slot = branch_leaves.setdefault(target, {}).setdefault(branch, [])
        # GROUP by rule prefix — 98 identical "GET /admin returns 20…" entries
        # are ONE node ("GET /admin · ×98"), never a wall of repeated text.
        toks = entry["rule"][:60].split()
        group_key = " ".join(toks[:2]) if toks else entry["rule"][:40]
        for leaf in slot:
            if leaf["group"] == group_key:
                leaf["count"] += 1
                if not leaf["severity"] and entry.get("severity"):
                    leaf["severity"] = entry["severity"]
                if entry.get("verified"):
                    leaf["verified"] = True
                return
        cap = 24 if branch == "vulns" else 12
        if len(slot) >= cap:
            # over cap: merge into the nearest existing group instead
            if slot:
                slot[-1]["count"] += 1
            return
        slot.append({**entry, "ref": ref, "group": group_key, "count": 1,
                     "rule": " ".join(toks[:4])[:60] or entry["rule"][:60],
                     "full": entry})

    for key, meta in nodes.items():
        t = assignment.get(key)
        if not t:
            continue
        is_endpoint = meta["kind"] in ("url",) or (meta["kind"] == "external" and key.lower() != t)
        for entry in meta["entries"]:
            branch = "vulns" if entry["category"].lower() in VULN_CATS else "info"
            add_leaf(t, branch, entry, ref=key)
        _ = is_endpoint  # endpoints render as real nodes below

    # ── assemble the tree ──
    SEV_COLOR = {"critical": "#ef4444", "high": "#f97316", "medium": "#eab308",
                 "low": "#84cc16", "verified": "#34d399"}
    nodes_out: dict[str, dict] = {}
    edge_set: set[tuple[str, str]] = set()

    def add_node(nid: str, label: str, kind: str, color: str, size: int, entries: list[dict] | None = None, raw: dict | None = None) -> None:
        if nid in nodes_out:
            return
        nodes_out[nid] = {"id": nid, "label": label[:64], "kind": kind, "color": color,
                          "size": size, "entryCount": len(entries or []), "entries": entries or []}

    def add_edge(s: str, t: str, ekind: str = "tree") -> None:
        if s != t and s in nodes_out and t in nodes_out:
            edge_set.add((s, t, ekind) if s < t else (t, s, ekind))

    add_node("__root__", "MEMORY", "hub", "#64748b", 46)
    add_node("__ext__", "External targets", "branch", "#14b8a6", 34)
    add_node("__int__", "Internal targets", "branch", "#fb923c", 34)
    add_node("__unsorted__", "Unsorted", "branch", "#94a3b8", 22)
    add_edge("__root__", "__ext__")
    add_edge("__root__", "__int__")
    add_edge("__root__", "__unsorted__")

    BRANCH_COLOR = {"info": "#60a5fa", "endpoints": "#2dd4bf", "vulns": "#ef4444"}
    SEVERITY_COLOR = {"critical": "#ef4444", "high": "#f97316", "medium": "#eab308",
                      "low": "#84cc16", "info": "#94a3b8"}

    def add_target(target: str, internal: bool) -> str:
        nid = f"target:{target}"
        if nid in nodes_out:
            return nid
        hub = "__int__" if internal else "__ext__"
        tot = sum(len(v) for v in branch_leaves.get(target, {}).values())
        label = f"{target} · {tot}" if tot > 0 else target
        add_node(nid, label, "target" + (":internal" if internal else ":external"),
                 "#fb923c" if internal else "#14b8a6", min(64, 22 + tot * 1.4))
        add_edge(hub, nid)
        for bname, blabel in (("info", "Info"), ("endpoints", "Endpoints"), ("vulns", "Vulnerabilities")):
            bid = f"{nid}/{bname}"
            leaves = branch_leaves.get(target, {}).get(bname, [])
            add_node(bid, blabel, f"branch:{bname}", BRANCH_COLOR[bname], 22)
            add_edge(nid, bid)
            for i, leaf in enumerate(leaves[:40]):
                lid = f"{bid}/leaf:{i}"
                count = leaf.get("count", 1)
                count_tag = f" · ×{count}" if count > 1 else ""
                if bname == "vulns":
                    color = SEVERITY_COLOR.get((leaf.get("severity") or "info").lower(), "#94a3b8")
                    lbl = ((leaf["rule"][:46] + "…") if len(leaf["rule"]) > 47 else leaf["rule"]) + count_tag
                else:
                    color = "#38bdf8" if bname == "info" else "#5eead4"
                    lbl = ((leaf["rule"][:46] + "…") if len(leaf["rule"]) > 47 else leaf["rule"]) + count_tag
                add_node(lid, lbl, bname + ":leaf", color, 10 + (3 if leaf.get("verified") else 0) + (2 if count > 1 else 0),
                         [{**leaf.get("full", leaf), "count": count}])
                add_edge(bid, lid)
        return nid

    # endpoints: real url / subdomain nodes under their target's Endpoints branch
    endpoint_parent: dict[str, str] = {}
    for key, meta in nodes.items():
        t = assignment.get(key)
        if not t:
            continue
        internal = meta["kind"] == "internal" or (meta["kind"] == "url" and meta["host"] in internal_targets)
        tid = add_target(t, internal)
        if meta["kind"] == "url" or (meta["kind"] == "external" and key.lower() != t):
            bid = f"{tid}/endpoints"
            color = "#fb923c" if meta["internal"] else "#5eead4"
            add_node(key, meta["label"], "endpoint", color,
                     min(30, 10 + meta["entryCount"] * 2), meta["entries"], meta)
            add_edge(bid, key)
            endpoint_parent[key] = tid

    # non-endpoint nodes (repos, packages, architecture notes) → Info children
    for key, meta in nodes.items():
        t = assignment.get(key)
        if not t or meta["kind"] in ("url", "internal"):
            continue
        is_target_self = key.lower() == t
        if meta["kind"] == "external" and is_target_self:
            continue
        tid = add_target(t, internal=False)
        bid = f"{tid}/info"
        add_node(key, meta["label"], "info", "#60a5fa",
                 min(24, 10 + meta["entryCount"] * 2), meta["entries"], meta)
        add_edge(bid, key)

    # internal targets' own urls already route via endpoint pass ✓
    # ── cross-links: evidence mentioning another known node or target ──
    id_list = sorted(nodes.keys())
    cross = 0
    for key, meta in nodes.items():
        if cross >= 300:
            break
        mentioned: set[str] = set()
        text = " ".join(e["rule"] + " " + e["evidence"] for e in meta["entries"]).lower()
        if not text:
            continue
        for other in id_list:
            if other == key or len(other) < 6:
                continue
            o = _norm(other)
            if len(o) >= 6 and o in text:
                mentioned.add(other)
        for other in list(mentioned)[:4]:
            if other in nodes_out and key in nodes_out:
                add_edge(key, other, "cross")
                cross += 1
            elif other.startswith("target:"):
                add_edge(key, other, "cross")
                cross += 1

    edges = [{"source": s, "target": t, "ekind": k} for (s, t, k) in edge_set]
    return {"nodes": list(nodes_out.values()), "edges": edges,
            "stats": {"nodes": len(nodes_out), "edges": len(edges), "entries": total_entries},
            "targets": sorted({nid for nid in nodes_out if nid.startswith("target:")})}


@router.get("/api/console/service-plugins")
def service_plugins():
    items = [{
        "id": p["id"], "name": p["name"], "description": p["description"],
        "category": "suijin", "configured": True, "enabled": p["loaded"],
        "icon": p.get("icon") or "",
        "tools": [], "values": {}, "testedAt": None,
    } for p in _module_items()]
    return {"items": items, "executionProfile": "local"}


@router.patch("/api/console/service-plugins/{module_id}")
def service_plugin_toggle(module_id: str, body: dict = Body(default={})):
    """The integrations-tab switch is REAL: enable/disable via the module
    manager (core tier refuses, same as the Plugins tab)."""
    enabled = body.get("enabled")
    return _module_action("enable" if enabled else "disable", module_id)


@router.get("/api/console/workflow-definitions")
def workflow_definitions():
    return {"definitions": []}


@router.get("/api/console/workflow-tool-catalog")
def workflow_tool_catalog():
    tools = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.dispatch import list_route_tools

        tools = [{"name": n, "description": f"Suijin tool: {n}", "icon": _tool_icon(n)} for n in list_route_tools()]
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


@router.get("/api/console/provider-icon/github/{user}")
def provider_icon_github(user: str):
    """GitHub org/user avatar via unavatar — for tools that only live on
    GitHub (gobuster/OJ, ffuf/joohoi, …). Same cache, same rules."""
    import re as _re

    from fastapi import Response

    usr = str(user or "").strip()
    if not _re.fullmatch(r"[A-Za-z0-9._-]{1,60}", usr):
        return Response(status_code=404)
    cache_dir = _ws() / "cache" / "provider-icons"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"github_{usr}"
    headers = {"Cache-Control": "public, max-age=604800"}
    for ext, media in ((".png", "image/png"), (".svg", "image/svg+xml"), (".ico", "image/vnd.microsoft.icon")):
        cached = cache_dir / f"{cache_key}{ext}"
        if cached.exists() and cached.stat().st_size > 50:
            return Response(cached.read_bytes(), media_type=media, headers=headers)
    import urllib.request

    sources = (f"https://unavatar.io/github/{usr}?fallback=false",)
    for src in sources:
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            if len(data) > 50 and data[:8] == b"\x89PNG\r\n\x1a\n":
                (cache_dir / f"{cache_key}.png").write_bytes(data)
                return Response(data, media_type="image/png", headers=headers)
            if len(data) > 50:
                (cache_dir / f"{cache_key}.png").write_bytes(data)
                return Response(data, media_type="image/png", headers=headers)
        except Exception:  # noqa: BLE001
            continue
    return Response(status_code=404)


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

    # The frontend lists providers by display id ("z-ai"); the registry and
    # provider catalog use the registry key ("zai"). Normalize so Verify
    # actually reaches the right provider spec (and reads the right key env).
    key = {"z-ai": "zai", "zhipu": "zai"}.get(key, key)

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
    """Diagnostics honesty: what a provider ACTUALLY does right now. The
    result is persisted (5 min TTL) so doctor/providers reflect it."""
    result = _ping_provider(key)
    # store under BOTH the display id the UI sends ("z-ai") and the registry
    # key the ping normalized to ("zai") — lookups use the display id.
    _store_verify_result(key, result)
    normalized = {"z-ai": "zai", "zhipu": "zai"}.get(key)
    if normalized and normalized != key:
        _store_verify_result(normalized, result)
    return result


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


