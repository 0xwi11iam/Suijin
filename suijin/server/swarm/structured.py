"""structured mode — the M1 leader → M2 supervisors → M3 workers hierarchy.

Topology (operator spec, 2026-11-14):
  1 leader (M1, strongest model — REQUIRED)
  → 3 categories: recon, exploitation, coder
    → each: 1 supervisor (M2) per up-to-4 workers (M3)

- the leader DIRECTS SUPERVISORS ONLY (never workers directly)
- supervisors are REAL AI: reading worker traffic (mesh_read), sharing
  data between workers, DMing course corrections (mesh_dm), reporting
  to the leader every ~3 minutes (the watchdog forces the timer)
- every agent holds the full prompt and can step in when needed
- model cascade: worker falls back m3→m2→m1 (model_routing resolves)
- single node type per structured engagement (host OR box)
- expandable: more categories / deeper nesting later

Comm channels are the EXISTING mesh: mesh_dm / mesh_read /
mesh_broadcast agent tools. No new transport.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

CATEGORIES = ["recon", "exploitation", "coder"]
SUPERVISOR_REPORT_S = 180  # the ~3-minute report timer


def _root() -> Path:
    with contextlib.suppress(Exception):
        from suijin.server.platform.lib.workspace import WORKSPACE_DIR

        return Path(WORKSPACE_DIR).parent
    return Path.home() / ".suijin"


def _track_hunt_pid(pid: int) -> None:
    """Record a spawned agent pid — the web server kills orphans from
    this file at boot (a restart leaves PPID-1 agents running forever)."""
    with contextlib.suppress(OSError):
        p = _root() / "swarm" / "console-agents.pids"
        existing = []
        if p.exists():
            existing = [int(x) for x in p.read_text().split() if x.strip().isdigit()]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(" ".join(str(x) for x in set(existing + [pid])))


def _slug(title: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", str(title or "hunt").lower()).strip("-")
    return (s or "hunt")[:48]


def _tier_models() -> dict:
    """model_1 (leader, required) → model_2 (supervisor, default m1)
    → model_3 (worker, default m2). Read from config.json."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        cfg = load_config() or {}
        m1 = str(cfg.get("model_1") or "")
        m2 = str(cfg.get("model_2") or m1)
        m3 = str(cfg.get("model_3") or m2)
        return {"m1": m1, "m2": m2, "m3": m3}
    return {"m1": "", "m2": "", "m3": ""}


def _register(identity: str, swarm: str, lane: str) -> None:
    """The hierarchy must be VISIBLE in the swarm board (console shows
    the structured tree via agents table lanes). identity is NOT NULL in
    the schema — set it or the insert silently fails."""
    with contextlib.suppress(Exception):
        db = sqlite3.connect(str(_root() / "swarm" / "swarm.db"), timeout=5)
        db.execute(
            "INSERT INTO agents (id, identity, swarm, current_intent, last_heartbeat, status) "
            "VALUES (?, ?, ?, ?, ?, 'active') "
            "ON CONFLICT(id) DO UPDATE SET swarm=excluded.swarm, "
            "current_intent=excluded.current_intent, last_heartbeat=excluded.last_heartbeat",
            (identity, identity, swarm, lane, time.time()),
        )
        db.commit()
        db.close()


_current_swarm = ["default"]


def _spawn_tiered(port: int, identity: str, mission: str, tier: str, model: str) -> int | None:
    """One structured agent as a background host process. tier drives the
    model via SUIJIN_TIER (the worker loop resolves the cascade)."""
    import subprocess
    import sys
    import tempfile

    log_file = Path(tempfile.gettempdir()) / f"suijin-agent-{identity}.log"
    env = dict(os.environ)
    env["SUIJIN_TIER"] = tier
    if model:
        env["SUIJIN_TIER_MODEL"] = model
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "suijin.server.swarm.agent",
         "--coordinator", f"127.0.0.1:{port}", "--identity", identity,
         "--mission", mission, "--swarm", _current_swarm[0]],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    return proc.pid


def _steer(port: int, identity: str, message: str) -> bool:
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient

        bc = BusClient()
        if bc.connect("127.0.0.1", port, f"structured-{os.urandom(2).hex()}"):
            bc.publish(f"/agent/{identity}/steer", {"message": message[:500]})
            bc.close()
            return True
    return False


def start_structured(title: str, workers_per_cat: int = 2, node: str = "host",
                     boxed_n: int = 0) -> dict:
    """Build the hierarchy and spawn it. Returns the topology dict."""
    title = str(title or "").strip() or "structured hunt"
    slug = _slug(title)
    workers_per_cat = max(1, min(8, int(workers_per_cat or 2)))
    sup_per_cat = 1 + (workers_per_cat // 4)  # +1 supervisor per 4 workers

    from suijin.server.swarm.lifecycle import _bus_addr, _wait_for_schema

    addr = _bus_addr()
    if not addr:
        import subprocess as _sp
        import sys as _sys

        _sp.run([_sys.executable, "-m", "suijin.server.swarm.coordinator"],
                check=False, capture_output=True, timeout=60)
        time.sleep(2)
        addr = _bus_addr()
    if not addr:
        return {"ok": False, "error": "no coordinator — could not start one"}
    port = addr[1]

    tiers = _tier_models()
    if not tiers["m1"]:
        return {"ok": False,
                "error": "model_1 is REQUIRED for structured mode — set it in settings"}

    _current_swarm[0] = slug
    leader_id = f"m1-leader-{slug[:20]}-{os.urandom(2).hex()}"
    sup_ids: dict[str, list[str]] = {}
    worker_ids: dict[str, list[str]] = {}

    topology = {
        "ok": True, "engagement": slug, "title": title, "node": node,
        "leader": leader_id, "tiers": tiers,
        "supervisors": {}, "workers": {}, "spawned": 0,
    }

    def _mission_leader() -> str:
        sup_list = ", ".join(s for ids in sup_ids.values() for s in ids)
        return (f"{title}\n\n"
                f"You are the M1 LEADER of a structured swarm. You DIRECT "
                f"SUPERVISORS ONLY — never workers directly. Supervisors: "
                f"{sup_list}. Read their reports (mesh_read), direct them "
                f"(mesh_dm), hold the strategy. If a supervisor stalls, "
                f"step in — you hold the full prompt.")

    def _mission_sup(cat: str, my_workers: list[str], me: str) -> str:
        wl = ", ".join(my_workers)
        return (f"supervise {cat} for: {title}\n\n"
                f"You are an M2 SUPERVISOR. Your workers: {wl}. Read what "
                f"they are doing (mesh_read), analyze it, share data between "
                f"them, DM course corrections (mesh_dm). Report to the "
                f"leader {leader_id} every ~3 minutes (mesh_dm to "
                f"{leader_id}). Relay the leader's directives to your "
                f"workers. You hold the full prompt — step in when a "
                f"worker stalls.")

    def _mission_worker(cat: str, sup: str) -> str:
        return (f"{cat} work for: {title}\n\n"
                f"You are an M3 WORKER in category {cat}. Execute and "
                f"report facts. Your supervisor is {sup} — follow its "
                f"course corrections.")

    # spawn order: workers → supervisors (missions need worker ids) → leader
    for cat in CATEGORIES:
        sup_ids[cat] = []
        worker_ids[cat] = []
        for i in range(workers_per_cat):
            wid = f"m3-worker-{cat}-{i + 1}-{os.urandom(2).hex()}"
            worker_ids[cat].append(wid)
        for s in range(sup_per_cat):
            sup_ids[cat].append(f"m2-sup-{cat}-{s + 1}-{os.urandom(2).hex()}")

    def _spawn_one(identity: str, mission: str, tier: str, model: str, lane: str) -> None:
        if node == "box":
            with contextlib.suppress(Exception):
                from suijin.server.box import hypervisor as hv

                from suijin.server.swarm.lifecycle import _bus_addr

                box_addr = hv.coordinator_addr_for_boxes(_bus_addr()[1])
                if hv.spawn_sandbox_agent(box_addr, identity, slug, mission=mission):
                    _register(identity, slug, lane)
                    topology["spawned"] += 1
                    return
        pid = _spawn_tiered(port, identity, mission, tier, model)
        if pid:
            _register(identity, slug, lane)
            topology["spawned"] += 1

    for cat in CATEGORIES:
        for i, wid in enumerate(worker_ids[cat]):
            sup = sup_ids[cat][min(i, len(sup_ids[cat]) - 1)]
            _spawn_one(wid, _mission_worker(cat, sup), "worker", tiers["m3"],
                       f"m3 worker · {cat}")
        for me in sup_ids[cat]:
            _spawn_one(me, _mission_sup(cat, worker_ids[cat], me), "supervisor",
                       tiers["m2"], f"m2 supervisor · {cat}")
    _spawn_one(leader_id, _mission_leader(), "leader", tiers["m1"], "m1 leader")

    topology["supervisors"] = sup_ids
    topology["workers"] = worker_ids

    # persist the registry (status display + watchdog state)
    reg = _root() / "swarm" / f"structured-{slug}.json"
    with contextlib.suppress(OSError):
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({
            "title": title, "slug": slug, "node": node,
            "leader": leader_id, "supervisors": sup_ids, "workers": worker_ids,
            "tiers": tiers, "started_at": time.time(),
        }, indent=2))

    _wait_for_schema_safe()
    threading.Thread(target=_watchdog, args=(slug, port, dict(topology)),
                     daemon=True, name=f"structured-{slug}").start()
    return topology


def _wait_for_schema_safe() -> None:
    with contextlib.suppress(Exception):
        from suijin.server.swarm.lifecycle import _wait_for_schema

        _wait_for_schema()


def _watchdog(slug: str, port: int, topology: dict) -> None:
    """Force the ~3-minute supervisor report: any supervisor silent for
    a full cycle gets steered to report now (the timer forces it even
    when the supervisor's own judgment doesn't)."""
    leader = topology["leader"]
    sups = {s for ids in topology["supervisors"].values() for s in ids}
    last_report: dict[str, float] = {s: time.time() for s in sups}
    events = _root() / "engagements" / (slug if slug != "default" else "_default") / "events.jsonl"
    while True:
        time.sleep(60)
        # re-assert the hierarchy rows (registration must never flatten them)
        with contextlib.suppress(Exception):
            db = sqlite3.connect(str(_root() / "swarm" / "swarm.db"), timeout=5)
            for s in sups:
                db.execute("UPDATE agents SET swarm = ? WHERE id = ?", (slug, s))
            db.execute("UPDATE agents SET swarm = ? WHERE id = ?", (slug, leader))
            db.commit()
            db.close()
        with contextlib.suppress(OSError):
            mtime = events.stat().st_mtime
            cutoff = time.time() - SUPERVISOR_REPORT_S - 30
            if mtime >= cutoff:  # fresh traffic — someone reported/acted
                for s in sups:
                    last_report[s] = max(last_report.get(s, 0), mtime)
        now = time.time()
        for s in sups:
            if now - last_report.get(s, 0) > SUPERVISOR_REPORT_S + 30:
                _steer(port, s, "watchdog: report to the leader now — "
                       f"mesh_dm {leader} with your category status, "
                       "findings and what your workers are doing.")
                last_report[s] = now


def resume_watchdogs() -> int:
    """Re-arm watchdogs for every structured registry (web server boot)."""
    n = 0
    for reg in status_structured():
        with contextlib.suppress(Exception):
            addr = _bus_addr_safe()
            if not addr:
                break
            t = json.loads(reg["registry"] and Path(reg["registry"]).read_text())
            threading.Thread(target=_watchdog, args=(t["slug"], addr[1],
                             {"leader": t["leader"], "supervisors": t["supervisors"]}),
                             daemon=True, name=f"structured-{t['slug']}").start()
            n += 1
    return n


def _bus_addr_safe():
    with contextlib.suppress(Exception):
        from suijin.server.swarm.lifecycle import _bus_addr

        return _bus_addr()
    return None


def status_structured() -> list[dict]:
    """Live structured engagements (registry files with fresh agents)."""
    out = []
    for reg in (_root() / "swarm").glob("structured-*.json"):
        with contextlib.suppress(Exception):
            d = json.loads(reg.read_text())
            d["registry"] = str(reg)
            out.append(d)
    return out
