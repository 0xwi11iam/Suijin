"""suijin.server.data — THE data layer for every client.

Screens and commands used to scrape their own state (swarm.db here,
docker there, session files elsewhere) — every client a little
different, every bug its own. Now there is ONE aggregator and TWO
transports:

  local     from suijin.server.data import query
  over bus  BusClient.request("/data/query", {"q": "swarm"})
            → the coordinator computes and replies (same answers for
              boxed agents and any future remote client)

Queries: swarm · boxes · sessions · engagement · system · all.
Every query degrades: a dead docker engine or locked db yields honest
partial data, never an exception into a screen.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

HB_DEAD_S = 30


def _ws_root() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).parent
    return Path.home() / ".suijin"


# ── swarm ────────────────────────────────────────────────────────


def _q_swarm() -> dict:
    out = {"live": False, "agents": [], "intents": {}, "findings": [], "facts_1h": 0}
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus

        out["live"] = bool(discover_bus())
    now = time.time()
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_ws_root() / "swarm" / "swarm.db"), timeout=3)
        for r in db.execute(
            "SELECT id, current_intent, last_heartbeat FROM agents ORDER BY last_heartbeat DESC"
        ).fetchall():
            age = int(now - (r[2] or 0))
            out["agents"].append({
                "id": r[0], "lane": r[1] or "idle", "hb_age": age,
                "boxed": "-sbx" in r[0], "dead": age > HB_DEAD_S,
            })
        for r in db.execute("SELECT state, COUNT(*) FROM intents GROUP BY state").fetchall():
            out["intents"][r[0]] = r[1]
        for r in db.execute(
            "SELECT severity, status, title, agent_id, created_at FROM findings ORDER BY created_at DESC LIMIT 20"
        ).fetchall():
            out["findings"].append({
                "severity": r[0] or "?", "status": r[1] or "?",
                "title": (r[2] or "")[:120], "agent": r[3] or "", "age_s": int(now - (r[4] or now)),
            })
        out["facts_1h"] = db.execute(
            "SELECT COUNT(*) FROM facts WHERE created_at > ?", (now - 3600,)
        ).fetchone()[0]
        db.close()
    out["n_local"] = sum(1 for a in out["agents"] if not a["boxed"] and not a["dead"])
    out["n_boxed"] = sum(1 for a in out["agents"] if a["boxed"] and not a["dead"])
    return out


# ── boxes ────────────────────────────────────────────────────────


def _q_boxes() -> dict:
    out = {"available": False, "boxes": [], "pool_n": 0, "pool_target": 4,
           "image": "", "fresh": None, "engine": "none"}
    with contextlib.suppress(Exception):
        from suijin.server.box import hypervisor as hv

        out["available"] = hv.engine_available()
        if out["available"]:
            out["boxes"] = hv.sandbox_ls()
            out["pool_n"] = len(hv.pool_members())
            out["pool_target"] = hv.pool_target()
            out["image"] = hv.image_tag()
            out["fresh"] = hv.image_is_fresh() if hv.image_exists() else None
            out["engine"] = hv._engine_bin()
    for b in out["boxes"]:
        b["running"] = "up" in (b.get("status") or "").lower()
        b["pooled"] = "pool" in (b.get("name") or "")
    return out


# ── sessions ─────────────────────────────────────────────────────


def _q_sessions() -> dict:
    out = {"sessions": []}
    d = _ws_root() / "config" / "sessions"
    now = time.time()
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        with contextlib.suppress(Exception):
            state = json.loads(f.read_text())
            out["sessions"].append({
                "name": f.stem,
                "cookies": len(state.get("cookies", [])),
                "age_h": round((now - f.stat().st_mtime) / 3600, 1),
            })
    return out


# ── engagement data ──────────────────────────────────────────────


def _q_engagement(engagement: str = "_default") -> dict:
    out = {"engagement": engagement, "files_n": 0, "last_write_age_s": -1, "reports": []}
    d = _ws_root() / "engagements" / engagement
    with contextlib.suppress(Exception):
        files = [p for p in d.rglob("*") if p.is_file()] if d.exists() else []
        out["files_n"] = len(files)
        if files:
            out["last_write_age_s"] = int(now := time.time() - max(p.stat().st_mtime for p in files))
        out["reports"] = [p.name for p in (d / "reports").glob("*")][:10] if (d / "reports").exists() else []
    return out


# ── system ───────────────────────────────────────────────────────


def _q_system() -> dict:
    out = {"cpu": "n/a", "mem": "n/a", "provider": "", "model": "",
           "turn_cap": 0, "autopilot": True, "spend_calls": 0}
    with contextlib.suppress(Exception):
        import psutil

        out["cpu"] = f"{psutil.cpu_percent(interval=None):.0f}%"
        vm = psutil.virtual_memory()
        out["mem"] = f"{vm.used / (1024**3):.1f}/{vm.total / (1024**3):.1f} gb"
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        cfg = load_config() or {}
        out["provider"] = str(cfg.get("provider") or "")
        out["model"] = str(cfg.get(f"{cfg.get('provider', '')}_model") or cfg.get("final_model_id") or "")
        out["turn_cap"] = int(cfg.get("swarm_turn_cap") or 0)
    with contextlib.suppress(Exception):
        from suijin.server.providers.lib import get_usage

        u = get_usage()
        out["spend_calls"] = u.get("calls", 0)
        out["tokens_in"] = u.get("input_tokens", 0)
        out["tokens_out"] = u.get("output_tokens", 0)
    return out


# ── the one entry point ──────────────────────────────────────────


def query(q: str, engagement: str = "_default") -> dict:
    """One organized pull for any client. Unknown query → honest error
    dict, never an exception."""
    try:
        if q == "swarms":
            rows = []
            with contextlib.suppress(Exception):
                db = sqlite3.connect(str(_ws_root() / "swarm" / "swarm.db"), timeout=3)
                now = time.time()
                names = {r[0] or "default" for r in db.execute("SELECT swarm FROM intents").fetchall()}
                for s in sorted(names):
                    live = db.execute(
                        "SELECT COUNT(*) FROM agents WHERE last_heartbeat > ? AND (swarm = ? OR swarm IS NULL OR swarm = '')",
                        (now - HB_DEAD_S, s),
                    ).fetchone()[0]
                    open_n = db.execute("SELECT COUNT(*) FROM intents WHERE state='open' AND swarm = ?", (s,)).fetchone()[0]
                    run_n = db.execute("SELECT COUNT(*) FROM intents WHERE state='running' AND swarm = ?", (s,)).fetchone()[0]
                    rows.append({"swarm": s, "agents": live, "open": open_n, "running": run_n})
                db.close()
            return {"ok": True, "q": "swarms", "swarms": rows}
        if q == "swarm":
            return {"ok": True, "q": q, **_q_swarm()}
        if q == "boxes":
            return {"ok": True, "q": q, **_q_boxes()}
        if q == "sessions":
            return {"ok": True, "q": q, **_q_sessions()}
        if q == "engagement":
            return {"ok": True, "q": q, **_q_engagement(engagement)}
        if q == "system":
            return {"ok": True, "q": q, **_q_system()}
        if q == "all":
            return {"ok": True, "q": "all",
                    "swarm": _q_swarm(), "boxes": _q_boxes(), "sessions": _q_sessions(),
                    "engagement": _q_engagement(engagement), "system": _q_system()}
        return {"ok": False, "error": f"unknown query '{q}'",
                "known": ["swarm", "boxes", "sessions", "engagement", "system", "all"]}
    except Exception as e:  # noqa: BLE001 — a data layer never crashes a screen
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
