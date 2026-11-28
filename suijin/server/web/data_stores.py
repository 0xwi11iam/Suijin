"""The stores Suijin keeps, declared once for the data hub (see data_hub.py).

A store is one place Suijin keeps data: a table, a file, a folder, a registry. Each declaration
says where it is (relative to the runtime root or the repo — never an absolute machine path), what
it holds, how sensitive it is, and how to read it safely. Engagement folders come and go, so one
set of stores is registered per engagement.

Sensitivity:
  public    — shipped with the repo, or harmless on its own
  operator  — the operator's own state; shown in the console, masked where it can carry secrets
  secret    — credentials; only names and counts ever leave the process
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from suijin.server.web.data_hub import (
    OPERATOR,
    PUBLIC,
    SECRET,
    Store,
    directory_reader,
    json_reader,
    jsonl_reader,
    register,
    registry_reader,
    sqlite_reader,
    unregister,
)

# ── where things are ───────────────────────────────────────────────────────────────────────


def _cfg() -> Path:
    from suijin.server.platform.lib.workspace import WORKSPACE_DIR

    return Path(WORKSPACE_DIR)


def _rt() -> Path:
    base = _cfg()
    return base.parent if base.name == "config" else base


def _swarm_db() -> Path:
    return _rt() / "swarm" / "swarm.db"


def _engagements_root() -> Path:
    return _rt() / "engagements"


# ── small caches: the catalog is polled, so summaries are reused for a few seconds ───────────

_CACHE: dict[str, tuple[float, object]] = {}
_TTL = 5.0


def _cached(key: str, build, ttl: float = _TTL):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = build()
    _CACHE[key] = (time.time(), value)
    return value


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    total = 0
    with contextlib.suppress(OSError), path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            total += chunk.count(b"\n")
    return total


# ── config ─────────────────────────────────────────────────────────────────────────────────


def _env_names() -> list[dict]:
    """The .env's KEY names — never a value."""
    file = _cfg() / ".env"
    rows = []
    if file.is_file():
        for line in file.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                rows.append({"name": line.split("=", 1)[0].replace("export ", "").strip(), "set": True})
    return rows


def _credential_names() -> list[dict]:
    rows = []
    file = _cfg() / "credentials.json"
    if file.is_file():
        with contextlib.suppress(OSError, ValueError):
            for item in json.loads(file.read_text()).get("credentials") or []:
                if isinstance(item, dict):
                    rows.append(
                        {
                            "name": str(item.get("name") or item.get("id") or "credential"),
                            "kind": str(item.get("kind") or item.get("type") or ""),
                        }
                    )
    return rows


def _prompt_files() -> list[dict]:
    rows = []
    for name in ("prompt.md", "SOUL.md", "hunt-policy.md"):
        file = _cfg() / name
        if file.is_file():
            text = file.read_text(errors="replace")
            rows.append(
                {
                    "name": name,
                    "bytes": len(text.encode()),
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(file.stat().st_mtime)),
                    "lines": text.count("\n") + 1,
                    "preview": text[:160],
                    "content": text,
                }
            )
    return rows


def _skills() -> list[dict]:
    with contextlib.suppress(Exception):
        from suijin.server.web import console_api

        return list((console_api.skills_list() or {}).get("skills") or [])
    return []


# ── swarm ──────────────────────────────────────────────────────────────────────────────────


def _swarm_counts() -> dict:
    """One connection, a handful of counts — the swarm at a glance."""
    out = {
        "agents_total": 0,
        "agents_live": 0,
        "agents_paused": 0,
        "agents_stopped": 0,
        "intents_open": 0,
        "intents_running": 0,
        "intents_blocked": 0,
        "intents_done": 0,
        "findings": 0,
        "findings_confirmed": 0,
    }
    path = _swarm_db()
    if not path.is_file():
        return out
    conn = sqlite3.connect(str(path), timeout=2)
    try:
        now = time.time()
        for row in conn.execute("SELECT status, last_heartbeat FROM agents").fetchall():
            out["agents_total"] += 1
            if (row[1] or 0) > now - 90:
                out["agents_live"] += 1
            if str(row[0] or "") == "paused":
                out["agents_paused"] += 1
            if str(row[0] or "") == "stopped":
                out["agents_stopped"] += 1
        for state, count in conn.execute("SELECT state, COUNT(*) FROM intents GROUP BY state").fetchall():
            key = f"intents_{str(state or 'open').lower()}"
            out[key] = out.get(key, 0) + int(count)
        out["findings"] = int(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0])
        out["findings_confirmed"] = int(
            conn.execute("SELECT COUNT(*) FROM findings WHERE status = 'confirmed'").fetchone()[0]
        )
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def _bus_address() -> str:
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus

        found = discover_bus(verify=False)  # read the port file; never delete it on a transient miss
        return f"{found[0]}:{found[1]}" if found else ""
    return ""


def _coordinator_running() -> bool:
    """The coordinator has no pid file and no status call: it is up when the bus answers and an
    agent heartbeat or the schema says the swarm layer has been at work."""
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import discover_bus

        return bool(discover_bus(verify=True))
    return False


def _swarm_status() -> list[dict]:
    from suijin.server.platform.lib.config_loader import load_config

    cfg = {}
    with contextlib.suppress(Exception):
        cfg = load_config() or {}
    counts = _cached("swarm.counts", _swarm_counts)
    pause_flag = (_rt() / "mesh" / "pause-swarm").exists()
    routing = dict(cfg.get("model_routing") or {})
    m1, m2, m3 = str(cfg.get("model_1") or ""), str(cfg.get("model_2") or ""), str(cfg.get("model_3") or "")
    # the autopilot fires only when nothing is open or running anywhere (coordinator._autopilot_check)
    busy = counts["intents_open"] + counts["intents_running"] > 0
    return [
        {
            "coordinator": "running" if _coordinator_running() else "not running",
            "bus": _bus_address(),
            "agents_live": counts["agents_live"],
            "agents_paused": counts["agents_paused"],
            "intents_open": counts["intents_open"],
            "intents_running": counts["intents_running"],
            "findings": counts["findings"],
            "findings_confirmed": counts["findings_confirmed"],
            "autopilot": "blocked — intents are open or running"
            if busy
            else ("armed" if cfg.get("swarm_autopilot", True) not in (False, 0, "false") else "off"),
            "pause_flag": pause_flag,
            "leader": m2 if False else m1,
            "supervisor": m2 or m1,
            "worker": m3 or m2 or m1,
            "planner": routing.get("planner") or "",
            "worker_route": routing.get("worker") or "",
            "escalate_chars": routing.get("classify_threshold") or "",
        }
    ]


def _jobs() -> list[dict]:
    """Background jobs started in THIS process (the registry is in-memory per process)."""
    with contextlib.suppress(Exception):
        from suijin.server.tools.lib import job_registry

        rows = []
        for job in sorted(job_registry.list_jobs(), key=lambda item: item.get("started_at") or 0, reverse=True):
            rows.append({key: value for key, value in job.items() if not str(key).startswith("_")})
        return rows
    return []


def _structured_files() -> list[dict]:
    rows = []
    for file in sorted((_rt() / "swarm").glob("structured-*.json")) if (_rt() / "swarm").is_dir() else []:
        with contextlib.suppress(OSError, ValueError):
            rows.append(
                {
                    "file": file.name,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(file.stat().st_mtime)),
                    "hierarchy": json.loads(file.read_text(errors="replace")),
                }
            )
    return rows


def _tracked_pids() -> list[dict]:
    file = _rt() / "swarm" / "console-agents.pids"
    rows = []
    if file.is_file():
        for token in file.read_text().split():
            if token.isdigit():
                try:
                    os.kill(int(token), 0)
                    alive = True
                except OSError:
                    alive = False
                rows.append({"pid": int(token), "alive": alive})
    return rows


# ── engagements ────────────────────────────────────────────────────────────────────────────

ENGAGEMENT_SKIP = {"home", "tmp"}  # the agent's userland and scratch: big, and it can hold secrets


def _engagement_folders() -> list[Path]:
    """Engagement folders: ones that have metadata or a journal. An empty or leftover folder is not
    an engagement (the index applies the same rule)."""
    root = _engagements_root()
    folders: list[Path] = []
    if root.is_dir():
        folders.extend(
            folder
            for folder in root.iterdir()
            if folder.is_dir()
            and not folder.name.startswith(".")
            and ((folder / "engagement.json").is_file() or (folder / "events.jsonl").is_file())
        )
    with contextlib.suppress(Exception):
        from suijin.server.swarm import registry as _reg
        folders.extend(_reg.targets_root().glob("*/swarms/*"))
    return sorted((f for f in folders if f.is_dir()), key=lambda folder: folder.name)


def _engagement_summary(folder: Path) -> dict:
    def build() -> dict:
        meta: dict = {}
        meta_path = folder / "engagement.json"
        if meta_path.is_file():
            with contextlib.suppress(OSError, ValueError):
                meta = json.loads(meta_path.read_text())
        events = folder / "events.jsonl"
        files = 0
        size = 0
        latest = 0.0
        for path in folder.rglob("*"):
            if not path.is_file() or ENGAGEMENT_SKIP & set(path.relative_to(folder).parts[:-1]):
                continue
            files += 1
            try:
                stat = path.stat()
            except OSError:
                continue
            size += stat.st_size
            latest = max(latest, stat.st_mtime)
        catalog = folder / "exploits" / "catalog.json"
        exploits = 0
        if catalog.is_file():
            with contextlib.suppress(OSError, ValueError):
                exploits = len((json.loads(catalog.read_text()) or {}).get("entries") or {})
        return {
            "id": folder.name,
            "kind": str(meta.get("kind") or ("chat" if False else "engagement")) if meta else "legacy",
            "name": str(meta.get("name") or folder.name),
            "target": str(meta.get("target") or ""),
            "permissions": str(meta.get("permissions") or ""),
            "mode": str(meta.get("mode") or ""),
            "createdAt": str(meta.get("createdAt") or ""),
            "events": _count_lines(events),
            "exploits": exploits,
            "files": files,
            "bytes": size,
            "inbox": (folder / "inbox").is_dir(),
            "reports": (folder / "reports").is_dir() and any((folder / "reports").iterdir()),
            "notes": (folder / ".notes").is_dir(),
            "audit_trail": (folder / "audit_trails").is_dir(),
            "lastActivity": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(latest)) if latest else "",
        }

    return _cached(f"engagement.{folder.name}.summary", build, ttl=2.0)


def _engagement_findings() -> dict[str, int]:
    counts: dict[str, int] = {}

    def build() -> dict[str, int]:
        path = _swarm_db()
        if not path.is_file():
            return counts
        conn = sqlite3.connect(str(path), timeout=2)
        try:
            for engagement, count in conn.execute(
                "SELECT engagement, COUNT(*) FROM findings GROUP BY engagement"
            ).fetchall():
                counts[str(engagement)] = int(count)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
        return counts

    return _cached("engagement.findings", build)


def _engagement_rows() -> list[dict]:
    findings = _engagement_findings()
    rows = []
    for folder in _engagement_folders():
        row = _engagement_summary(folder)
        row["findings"] = findings.get(folder.name, 0)
        rows.append(row)
    rows.sort(key=lambda row: row.get("lastActivity") or "", reverse=True)
    return rows


def register_engagements() -> None:
    """One set of stores per engagement folder; folders that are gone are forgotten."""
    unregister("engagement")  # the id "engagement" itself does not exist; "engagement.<slug>.*" do
    for folder in _engagement_folders():
        base = f"engagement.{folder.name}"
        label = folder.name
        register(
            Store(
                f"{base}.events",
                "engagements",
                f"{label} — events",
                "jsonl",
                f"engagements/{folder.name}/events.jsonl",
                "The engagement's journal: every decision, tool call and result, in order.",
                OPERATOR,
                ["seq", "ts", "kind", "agent", "name", "id"],
                ["kind"],
                reader=jsonl_reader(lambda folder=folder: folder / "events.jsonl"),
            )
        )
        register(
            Store(
                f"{base}.files",
                "engagements",
                f"{label} — files",
                "directory",
                f"engagements/{folder.name}/",
                "Everything the engagement wrote — inbox, reports, notes, exploits, state. One file's content with ?name=.",
                OPERATOR,
                ["name", "bytes", "modified"],
                ["name"],
                reader=directory_reader(lambda folder=folder: folder, tail=20000, exclude=frozenset(ENGAGEMENT_SKIP)),
            )
        )
        register(
            Store(
                f"{base}.exploits",
                "engagements",
                f"{label} — exploit catalog",
                "json",
                f"engagements/{folder.name}/exploits/catalog.json",
                "Catalogued exploits: verdict, severity, score and every attempt.",
                OPERATOR,
                ["id", "title", "class", "severity", "cvss", "status", "attempts"],
                ["id", "class", "severity"],
                reader=registry_reader(lambda folder=folder: _exploit_rows(folder)),
                volatile=False,
            )
        )


def _exploit_rows(folder: Path) -> list[dict]:
    catalog = folder / "exploits" / "catalog.json"
    rows = []
    if catalog.is_file():
        with contextlib.suppress(OSError, ValueError):
            for exp_id, entry in (json.loads(catalog.read_text()) or {}).get("entries", {}).items():
                attempts = entry.get("attempts") or []
                rows.append(
                    {
                        "id": exp_id,
                        "title": entry.get("title"),
                        "class": entry.get("class"),
                        "target": entry.get("target"),
                        "severity": entry.get("severity"),
                        "cvss": entry.get("cvss"),
                        "status": entry.get("status"),
                        "created_at": entry.get("created_at"),
                        "attempts": len(attempts),
                        "last_verdict": (
                            attempts[-1].get("verdict") if attempts and isinstance(attempts[-1], dict) else ""
                        ),
                        "triage": entry.get("triage"),
                        "marker": entry.get("marker"),
                    }
                )
    return rows


# ── knowledge ──────────────────────────────────────────────────────────────────────────────

CONSTRAINT_TYPES = ("blocks", "rate_limit", "waf", "verified_cve", "false_positive", "behavior", "bypass", "verified")


def _knowledge_targets() -> list:
    with contextlib.suppress(Exception):
        from suijin.server.redteam.lib.intel import knowledge_graph as kg

        return [str(target) for target in (kg.get_all_targets() or [])]
    return []


def _knowledge_rows() -> list[dict]:
    """One row per target: how much the knowledge graph holds for it, by kind."""
    from suijin.server.redteam.lib.intel import knowledge_graph as kg

    rows = []
    for target in _knowledge_targets():
        counts: dict[str, int] = {}
        with contextlib.suppress(Exception):
            constraints = kg.get_constraints(target) or {}
            if isinstance(constraints, dict):
                for ctype, entries in constraints.items():
                    counts[str(ctype)] = len(entries) if isinstance(entries, (list, dict)) else 1
        rows.append({"target": target, "constraints": sum(counts.values()), **counts})
    return rows


def _knowledge_constraints() -> list[dict]:
    """Every constraint as a row. Evidence is masked: it has held keys in the past."""
    from suijin.server.redteam.lib.intel import knowledge_graph as kg

    rows = []
    for target in _knowledge_targets():
        with contextlib.suppress(Exception):
            constraints = kg.get_constraints(target) or {}
            if not isinstance(constraints, dict):
                continue
            for ctype, entries in constraints.items():
                items = (
                    entries.items()
                    if isinstance(entries, dict)
                    else enumerate(entries)
                    if isinstance(entries, list)
                    else []
                )
                for _key, item in items:
                    if isinstance(item, dict):
                        rows.append(
                            {
                                "target": target,
                                "type": str(ctype),
                                "rule": str(item.get("rule") or "")[:400],
                                "evidence": str(item.get("evidence") or "")[:400],
                                "confidence": item.get("confidence"),
                                "verified_at": item.get("verified_at"),
                                "last_seen": item.get("last_seen"),
                            }
                        )
    return rows


def _kg_status() -> list[dict]:
    """``backend_status()`` answers in words ("ladybug (embedded …) — UP"); the row keeps them."""
    with contextlib.suppress(Exception):
        from suijin.server.redteam.lib.intel import kg_backend

        status = str(kg_backend.backend_status() or "")
        return [
            {"backend": status.split("(")[0].strip(), "up": status.strip().upper().endswith("UP"), "status": status}
        ]
    return []


# ── tools, modules, mesh, system, logs ─────────────────────────────────────────────────────


def _tool_routes() -> list[dict]:
    rows = []
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib import dispatch
        from suijin.server.web import console_api

        packs = console_api._pack_tools()
        # list_route_tools() only. get_tool_catalog() builds the agents' whole 48 KB catalog — pack
        # guides and availability probes included — and took fourteen seconds on this machine.
        for name in dispatch.list_route_tools():
            pack, description = packs.get(name, ("", ""))
            rows.append({"name": name, "pack": pack, "description": description, "icon": console_api._tool_icon(name)})
    return rows


def _modules() -> list[dict]:
    with contextlib.suppress(Exception):
        from suijin.modules.manager import list_modules

        return [dict(item) for item in list_modules() if isinstance(item, dict)]
    return []


def _mesh_peers() -> list[dict]:
    with contextlib.suppress(Exception):
        from suijin.server.agent.lib import mesh

        return [dict(item) for item in mesh.peers() if isinstance(item, dict)]
    return []


def _mesh_chat() -> list[dict]:
    """The mesh group chat, newest first: `ts|src|eng|…|msg` lines."""
    file = _rt() / "mesh" / "gc.log"
    rows = []
    if file.is_file():
        with contextlib.suppress(OSError):
            for line in file.read_text(errors="replace").splitlines()[-400:]:
                parts = line.split("|")
                if len(parts) >= 3:
                    rows.append(
                        {"ts": parts[0], "src": parts[1], "engagement": parts[2], "message": "|".join(parts[3:])[:400]}
                    )
    rows.reverse()
    return rows


_SYSTEM: dict = {"at": 0.0, "value": {}, "sampling": False}
SYSTEM_SAMPLE_S = 5.0


def _sample_system() -> None:
    while True:
        with contextlib.suppress(Exception):
            from suijin.server.tools.lib.system_stats import collect

            _SYSTEM.update(at=time.time(), value=collect(interval=0.5))
        time.sleep(SYSTEM_SAMPLE_S)


def _system_stats() -> list[dict]:
    """The latest system sample, instantly. Collecting one takes about two seconds on macOS
    (reading disk counters), so it is never done on a request path: a background thread samples."""
    if not _SYSTEM["sampling"]:
        _SYSTEM["sampling"] = True
        threading.Thread(target=_sample_system, daemon=True, name="system-stats").start()
    if not _SYSTEM["value"]:
        return []
    return [{**_SYSTEM["value"], "sampled_age_s": round(time.time() - _SYSTEM["at"], 1)}]


def _bus_topology() -> list[dict]:
    """What travels on the mesh bus — publishers and subscribers as the code wires them."""
    rows = [
        {
            "topic": "/engagement/<eng>/events",
            "payload": "an events.jsonl record",
            "publishers": "EventLog, swarm agent",
            "subscribers": "TUI clients, web console",
            "effect": "journal, live",
        },
        {
            "topic": "/engagement/<eng>/work",
            "payload": "{agent, tool, turn, ts} · {agent, intent, state}",
            "publishers": "worker loop, swarm agent",
            "subscribers": "coordinator",
            "effect": "progress",
        },
        {
            "topic": "/engagement/<eng>/findings",
            "payload": "{severity, vuln_type, title, url, agent}",
            "publishers": "findings store, mesh",
            "subscribers": "swarm agent",
            "effect": "new finding",
        },
        {
            "topic": "/engagement/<eng>/facts",
            "payload": "{agent, summary, evidence}",
            "publishers": "swarm agent",
            "subscribers": "—",
            "effect": "fact recorded",
        },
        {
            "topic": "/engagement/<eng>/usage",
            "payload": "{agent, turn, calls, input_tokens, output_tokens}",
            "publishers": "swarm agent",
            "subscribers": "web console",
            "effect": "usage",
        },
        {
            "topic": "/engagement/<eng>/dead",
            "payload": "a dead-region entry",
            "publishers": "dead_regions",
            "subscribers": "—",
            "effect": "ledger",
        },
        {
            "topic": "/swarm/status",
            "payload": "{event: agent_joined|heartbeat|agent_dead|spawn_agents, agent, pid?, swarm?}",
            "publishers": "swarm agent, lifecycle",
            "subscribers": "coordinator, web console",
            "effect": "roster",
        },
        {
            "topic": "/cmd/pause · /cmd/resume",
            "payload": "{swarm} or {agent}",
            "publishers": "console routes, operator TUI",
            "subscribers": "agents",
            "effect": "hold / release between turns",
        },
        {
            "topic": "/cmd/stop · /cmd/delete",
            "payload": "{swarm} or {} (every agent)",
            "publishers": "console routes",
            "subscribers": "coordinator, agents",
            "effect": "exit at the next boundary",
        },
        {
            "topic": "/cmd/steer",
            "payload": "{message[:500], swarm?}",
            "publishers": "console routes, operator TUI",
            "subscribers": "agents",
            "effect": "operator interjection next turn",
        },
        {
            "topic": "/cmd/mission",
            "payload": "{mission[:500], swarm?|agent?}",
            "publishers": "—",
            "subscribers": "agents",
            "effect": "replaces the standing mission",
        },
        {
            "topic": "/agent/<id>/kill · /steer",
            "payload": "{} · {message[:500]}",
            "publishers": "lifecycle, console",
            "subscribers": "that agent",
            "effect": "exit / note",
        },
        {
            "topic": "/mesh/chat",
            "payload": "{line[:400], src, msg[:300]}",
            "publishers": "coordinator (gc.log bridge)",
            "subscribers": "—",
            "effect": "group chat",
        },
        {
            "topic": "/system/stats",
            "payload": "system_stats.collect()",
            "publishers": "web server, every 5 s",
            "subscribers": "—",
            "effect": "telemetry",
        },
        {
            "topic": "/data/query",
            "payload": "{q, engagement?, _rid, _reply_to} → {_rid, _result}",
            "publishers": "any client",
            "subscribers": "coordinator",
            "effect": "one organised pull",
        },
        {
            "topic": "/data/change",
            "payload": "{store, rows, at}",
            "publishers": "data hub",
            "subscribers": "any (plugins, console)",
            "effect": "a store changed",
        },
    ]
    return rows


# ── declaration ────────────────────────────────────────────────────────────────────────────

_DECLARED = False


def declare() -> None:
    global _DECLARED
    if _DECLARED:
        return
    _DECLARED = True

    # config — the operator's settings
    register(
        Store(
            "config.settings",
            "config",
            "Settings",
            "json",
            "config/config.json",
            "The operator's settings: providers, models, routing, guardrails, swarm. Secret-shaped keys are masked.",
            OPERATOR,
            ["setting", "value"],
            [],
            reader=json_reader(lambda: _cfg() / "config.json", ttl=30.0),
            volatile=False,
        )
    )
    register(
        Store(
            "config.env",
            "config",
            "Environment keys",
            "env",
            "config/.env",
            "Provider keys. Names only — a value never leaves the process.",
            SECRET,
            ["name", "set"],
            [],
            reader=registry_reader(_env_names),
            volatile=False,
        )
    )
    register(
        Store(
            "config.credentials",
            "config",
            "Credential vault",
            "json",
            "config/credentials.json",
            "Stored credentials. Names and kinds only.",
            SECRET,
            ["name", "kind"],
            [],
            reader=registry_reader(_credential_names),
            volatile=False,
        )
    )
    register(
        Store(
            "config.providers",
            "config",
            "Custom providers",
            "json",
            "config/providers.json",
            "Provider connection definitions.",
            OPERATOR,
            ["setting", "value"],
            [],
            reader=json_reader(lambda: _cfg() / "providers.json"),
            volatile=False,
        )
    )
    register(
        Store(
            "config.modules",
            "config",
            "Module switches",
            "json",
            "config/modules.json",
            "Which modules are switched off (absent means enabled).",
            PUBLIC,
            ["setting", "value"],
            [],
            reader=json_reader(lambda: _cfg() / "modules.json"),
            volatile=False,
        )
    )
    register(
        Store(
            "config.skills",
            "config",
            "Skills",
            "registry",
            "config/skills/<id>/SKILL.md",
            "The workspace skill library: name, description, category, tags.",
            PUBLIC,
            ["id", "name", "category", "tags"],
            [],
            reader=registry_reader(_skills, ttl=30.0),
            volatile=False,
        )
    )
    register(
        Store(
            "config.prompts",
            "config",
            "Prompt files",
            "markdown",
            "config/{prompt,SOUL,hunt-policy}.md",
            "The prompt, the soul and the hunt policy every agent rides with.",
            OPERATOR,
            ["name", "bytes", "lines"],
            [],
            reader=registry_reader(_prompt_files, ttl=30.0),
            volatile=False,
        )
    )
    for overlay in ("finding_triage", "finding_workflow", "finding_impact"):
        register(
            Store(
                f"config.{overlay}",
                "config",
                f"Review overlay — {overlay.split('_', 1)[1]}",
                "json",
                f"config/{overlay}.json",
                "The operator's review overlay for findings.",
                OPERATOR,
                ["setting", "value"],
                [],
                reader=json_reader(lambda overlay=overlay: _cfg() / f"{overlay}.json", ttl=30.0),
                volatile=False,
            )
        )
    register(
        Store(
            "config.authorizations",
            "config",
            "Authorizations",
            "directory",
            "config/authorizations/",
            "Signed scopes and authorizations, one file per program.",
            OPERATOR,
            ["name", "bytes", "modified"],
            ["name"],
            reader=directory_reader(lambda: _cfg() / "authorizations"),
            volatile=False,
        )
    )

    # swarm — the runtime
    tables = {
        "agents": (
            "Agents",
            "Every agent the swarm knows: lane, status, heartbeat, swarm.",
            ["id", "swarm", "status", "current_intent", "last_heartbeat", "joined_at"],
            ["swarm", "status"],
        ),
        "intents": (
            "Intents",
            "The work queue: what was asked, who holds it, and how it ended.",
            [
                "id",
                "text",
                "state",
                "agent_id",
                "swarm",
                "engagement",
                "attempts",
                "created_at",
                "claimed_at",
                "completed_at",
            ],
            ["state", "swarm"],
        ),
        "facts": (
            "Facts",
            "What agents observed, with the evidence behind it.",
            ["id", "summary", "agent_id", "engagement", "created_at"],
            [],
        ),
        "findings": (
            "Findings",
            "The findings queue: title, severity, status.",
            ["id", "title", "severity", "status", "engagement", "created_at"],
            ["severity", "status"],
        ),
        "findings_v2": (
            "Findings (detailed)",
            "Findings with description, evidence and submission details.",
            ["id", "program", "target", "title", "severity", "vrt_category", "status"],
            ["severity", "status"],
        ),
        "finding_submission_meta": (
            "Submission metadata",
            "Class, CWE, VRT and scoring metadata per finding.",
            ["finding_id", "class", "cwe", "vrt", "asset_type"],
            [],
        ),
        "rate_limits": ("Rate limits", "Per-host transport rate limits.", ["host", "tokens", "max_per_sec"], []),
    }
    for table, (label, description, columns, filterable) in tables.items():
        register(
            Store(
                f"swarm.{table}",
                "swarm",
                label,
                "sqlite-table",
                f"swarm/swarm.db:{table}",
                description,
                OPERATOR,
                columns,
                filterable,
                reader=sqlite_reader(_swarm_db, table),
            )
        )
    register(
        Store(
            "swarm.status",
            "swarm",
            "Swarm at a glance",
            "derived",
            "swarm/swarm.db + mesh bus",
            "Coordinator, bus, live agents, the queue, the autopilot and the fleet pause flag.",
            OPERATOR,
            ["coordinator", "bus", "agents_live", "intents_open", "autopilot", "pause_flag"],
            [],
            reader=registry_reader(_swarm_status),
        )
    )
    register(
        Store(
            "swarm.jobs",
            "swarm",
            "Background jobs",
            "registry",
            "this process",
            "Background jobs started from this console process (the registry is per process).",
            OPERATOR,
            ["job_id", "tool_name", "status", "started_at"],
            ["status"],
            reader=registry_reader(_jobs, ttl=2.0),
        )
    )
    register(
        Store(
            "swarm.structured",
            "swarm",
            "Structured hierarchies",
            "json",
            "swarm/structured-<slug>.json",
            "Leader / supervisor / worker hierarchies, one file per engagement.",
            OPERATOR,
            ["file", "modified"],
            [],
            reader=registry_reader(_structured_files, ttl=10.0),
            volatile=False,
        )
    )
    register(
        Store(
            "swarm.agents.pids",
            "swarm",
            "Tracked agent pids",
            "text",
            "swarm/console-agents.pids",
            "The pids of the agents this console is responsible for (swept at boot if orphaned).",
            PUBLIC,
            ["pid"],
            [],
            reader=registry_reader(_tracked_pids),
        )
    )

    # engagements — the index plus one set per folder
    register(
        Store(
            "engagements",
            "engagements",
            "Engagements",
            "derived",
            "engagements/<slug>/",
            "Every engagement and chat: kind, target, settings, and what it holds.",
            OPERATOR,
            ["id", "kind", "name", "target", "events", "findings", "exploits", "lastActivity"],
            ["kind"],
            reader=registry_reader(_engagement_rows),
        )
    )

    # knowledge
    register(
        Store(
            "knowledge.targets",
            "knowledge",
            "Knowledge targets",
            "derived",
            "intel/knowledge_graph",
            "What the knowledge graph holds, per target.",
            OPERATOR,
            ["target", "constraints"],
            [],
            reader=registry_reader(_knowledge_rows, ttl=10.0),
        )
    )
    register(
        Store(
            "knowledge.constraints",
            "knowledge",
            "Knowledge constraints",
            "derived",
            "intel/knowledge_graph",
            "Blocks, rate limits, WAF rules, verified CVEs, false positives and bypasses.",
            OPERATOR,
            ["target", "type", "rule", "confidence", "verified_at"],
            ["target", "type"],
            reader=registry_reader(_knowledge_constraints, ttl=10.0),
        )
    )
    register(
        Store(
            "knowledge.backend",
            "knowledge",
            "Knowledge backend",
            "derived",
            "intel/",
            "Which graph backend is in use and whether it answers.",
            OPERATOR,
            ["backend"],
            [],
            reader=registry_reader(_kg_status, ttl=10.0),
        )
    )

    # tools and modules
    register(
        Store(
            "tools.routes",
            "tools",
            "Tool routes",
            "registry",
            "dispatch.list_route_tools",
            "Every tool an agent can call, with its pack and description.",
            PUBLIC,
            ["name", "pack", "description"],
            [],
            reader=registry_reader(_tool_routes, ttl=60.0),
            volatile=False,
        )
    )
    register(
        Store(
            "tools.modules",
            "tools",
            "Modules",
            "registry",
            "modules/manager.list_modules",
            "Installed modules: version, tier, permissions, whether enabled.",
            PUBLIC,
            ["id", "version", "tier", "enabled", "description"],
            ["tier", "enabled"],
            reader=registry_reader(_modules, ttl=60.0),
            volatile=False,
        )
    )
    register(
        Store(
            "bus.topics",
            "tools",
            "Bus topics",
            "registry",
            "suijin/server/mesh/bus.py",
            "What travels on the mesh bus: topic, payload, publishers, subscribers, effect.",
            PUBLIC,
            ["topic", "payload", "publishers", "subscribers", "effect"],
            [],
            reader=registry_reader(_bus_topology, ttl=600.0),
            volatile=False,
        )
    )

    # mesh, system, logs
    register(
        Store(
            "mesh.peers",
            "mesh",
            "Mesh peers",
            "derived",
            "mesh/<pid>-state.json",
            "Agents and nodes present on the mesh.",
            OPERATOR,
            [],
            [],
            reader=registry_reader(_mesh_peers, ttl=5.0),
        )
    )
    register(
        Store(
            "mesh.chat",
            "mesh",
            "Mesh group chat",
            "text",
            "mesh/gc.log",
            "The group chat the agents share, newest first.",
            OPERATOR,
            ["ts", "src", "engagement", "message"],
            [],
            reader=registry_reader(_mesh_chat, ttl=2.0),
        )
    )
    register(
        Store(
            "mesh.files",
            "mesh",
            "Mesh files",
            "directory",
            "mesh/",
            "Presence, state, wire inbox and asks.",
            OPERATOR,
            ["name", "bytes", "modified"],
            ["name"],
            reader=directory_reader(lambda: _rt() / "mesh"),
        )
    )
    register(
        Store(
            "system.stats",
            "system",
            "System",
            "derived",
            "system_stats.collect",
            "CPU, memory, disk and network, sampled every few seconds by a background thread.",
            OPERATOR,
            [],
            [],
            reader=registry_reader(_system_stats, ttl=1.0),
        )
    )
    register(
        Store(
            "logs.files",
            "logs",
            "Logs",
            "directory",
            "logs/",
            "Suijin's logs: web, coordinator, agents, crashes. One file's tail with ?name=.",
            OPERATOR,
            ["name", "bytes", "modified"],
            ["name"],
            reader=directory_reader(lambda: _rt() / "logs", tail=12000),
        )
    )
    register(
        Store(
            "artifacts",
            "logs",
            "Artifacts",
            "directory",
            "artifacts/",
            "Pre-engagement artifacts.",
            OPERATOR,
            ["name", "bytes", "modified"],
            ["name"],
            reader=directory_reader(lambda: _rt() / "artifacts", tail=4000),
            volatile=False,
        )
    )
    register_engagements()


declare()
