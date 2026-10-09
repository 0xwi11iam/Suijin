"""Knowledge-graph backends — JSON today, Neo4j tomorrow, one switch apart.

Switch (config.json):
    "kg_backend": "neo4j",
    "neo4j_uri":      "bolt://localhost:7687",     # or env SUIJIN_NEO4J_URI
    "neo4j_user":     "neo4j",                    # or env SUIJIN_NEO4J_USER
    "neo4j_password": "..."                        # or env SUIJIN_NEO4J_PASSWORD

Default is "json" (the existing knowledge_graph.json — unchanged,
human-readable, git-friendly).

THE CONTRACT (this is what makes the switch trivial): every backend
returns the SAME shapes as the JSON file:

    get_constraints(target) -> {ctype: [{rule, evidence, confidence,
                                         verified_at, last_seen}, ...]}
    get_all_targets()       -> [target, ...]

Everything downstream (check_payload, check_cve, bypass strategies,
summary, mermaid export, the agent loop, the verifier) is written
against those shapes and never knows which backend answered.

Neo4j schema:
    (:Target {name})
    (:Constraint {rule, ctype, evidence, confidence, verified_at, last_seen})
    (t:Target)-[:HAS]->(c:Constraint)
One constraint node per (target, ctype, rule) — MERGE does the JSON
backend's dedup+confidence-max semantics natively.

The neo4j driver is imported lazily — the package is only required
when the backend is actually switched on.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("suijin.kg")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── JSON backend (lives here so knowledge_graph.py stays a thin shim) ─────


class JsonKG:
    """The current file-backed graph — moved verbatim, zero behavior change."""

    def __init__(self, path_fn) -> None:
        # path_fn: callable -> Path (resolved at EVERY access, so tests can
        # monkeypatch the module-level GRAPH_PATH and it stays honored)
        self._path_fn = path_fn

    @property
    def _path(self) -> Path:
        return self._path_fn()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt file reads as empty
            return {}

    def _save(self, data: dict) -> None:
        from suijin.modules.platform.lib.filelock import atomic_write

        atomic_write(self._path, json.dumps(data, indent=2, default=str))

    # ── contract methods ──────────────────────────────────────────────

    def add_constraint(self, target, ctype, rule, evidence="", confidence=1.0) -> None:
        # CROSS-PROCESS SAFE: concurrent sessions (two terminal windows)
        # read-modify-write this one file — the lock serializes the
        # critical section so a finding one session records cannot be
        # clobbered by the other's save. Without it, last-writer-wins
        # silently dropped findings (the multi-window bug).
        from suijin.modules.platform.lib.filelock import locked

        with locked(self._path):
            data = self._load()
            entry = data.setdefault(target, {})
            category = entry.setdefault(ctype, [])
            existing = [c for c in category if c.get("rule") == rule]
            if existing:
                existing[0]["evidence"] = evidence
                existing[0]["confidence"] = max(existing[0].get("confidence", 0), confidence)
                existing[0]["last_seen"] = _now()
            else:
                category.append(
                    {
                        "rule": rule,
                        "evidence": evidence,
                        "confidence": confidence,
                        "verified_at": _now(),
                        "last_seen": _now(),
                    }
                )
            entry["_updated"] = _now()
            self._save(data)

    def get_constraints(self, target) -> dict:
        return self._load().get(target, {})

    def get_all_targets(self) -> list:
        return [k for k in self._load() if not str(k).startswith("_")]

    def clear_target(self, target) -> None:
        data = self._load()
        data.pop(target, None)
        self._save(data)


# ── Neo4j backend (theory-complete; activate by switching config) ────────


class Neo4jKG:
    """Same contract, Cypher underneath.

    Not active by default — the operator flips kg_backend to "neo4j"
    once a server exists (bolt://...). All queries preserve the JSON
    semantics: MERGE dedups (target, ctype, rule), confidence only ever
    ratchets UP, last_seen refreshes on every re-observation.
    """

    def __init__(self, uri: str, user: str = "neo4j", password: str = "") -> None:
        self._uri = uri
        self._auth = (user, password)
        self._driver = None  # lazy: connected on first use, retried once per process

    # ── driver plumbing ───────────────────────────────────────────────

    def _connect(self):
        if self._driver is None:
            import neo4j  # lazy — optional dependency until the switch is flipped

            self._driver = neo4j.GraphDatabase.driver(self._uri, auth=self._auth)
            self._driver.verify_connectivity()
        return self._driver

    def _run(self, query: str, **params):
        driver = self._connect()
        with driver.session() as session:
            return list(session.run(query, **params))

    # ── contract methods ──────────────────────────────────────────────

    def add_constraint(self, target, ctype, rule, evidence="", confidence=1.0) -> None:
        # MERGE = the JSON backend's dedup; ON MATCH ratchets confidence up
        # and refreshes last_seen, mirroring existing[0] update semantics.
        self._run(
            """
            MERGE (t:Target {name: $target})
            MERGE (t)-[:HAS]->(c:Constraint {rule: $rule, ctype: $ctype})
            ON CREATE SET c.evidence = $evidence,
                          c.confidence = $confidence,
                          c.verified_at = datetime(),
                          c.last_seen = datetime(),
                          t._updated = datetime()
            ON MATCH SET  c.evidence = $evidence,
                          c.confidence = CASE WHEN c.confidence < $confidence
                                              THEN $confidence ELSE c.confidence END,
                          c.last_seen = datetime(),
                          t._updated = datetime()
            """,
            target=target,
            ctype=ctype,
            rule=rule,
            evidence=str(evidence),
            confidence=float(confidence),
        )

    def get_constraints(self, target) -> dict:
        # Groups flat constraint rows into the JSON shape: {ctype: [ {...} ]}
        rows = self._run(
            """
            MATCH (t:Target {name: $target})-[:HAS]->(c:Constraint)
            RETURN c.ctype AS ctype, c.rule AS rule, c.evidence AS evidence,
                   c.confidence AS confidence,
                   toString(c.verified_at) AS verified_at,
                   toString(c.last_seen) AS last_seen
            """,
            target=target,
        )
        out: dict = {}
        for r in rows:
            rec = dict(r) if not hasattr(r, "data") else r.data()
            out.setdefault(rec.get("ctype") or "?", []).append(
                {k: rec.get(k) for k in ("rule", "evidence", "confidence", "verified_at", "last_seen")}
            )
        return out

    def get_all_targets(self) -> list:
        rows = self._run("MATCH (t:Target) RETURN t.name AS name ORDER BY name")
        return [r["name"] for r in rows]

    def clear_target(self, target) -> None:
        # DETACH DELETE the target, then sweep orphaned constraints
        self._run(
            """
            MATCH (t:Target {name: $target})
            DETACH DELETE t
            """,
            target=target,
        )
        self._run(
            """
            MATCH (c:Constraint)
            WHERE NOT ()-[:HAS]->(c)
            DELETE c
            """
        )

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None


# ── backend selection ─────────────────────────────────────────────────────

_ENV_CACHE: dict = {"mtime": 0.0, "data": {}}


def _env_var(name: str) -> str:
    """os.environ first, then the .env on disk — the canonical secret
    store. Boot exports it, but ad-hoc entry points (console, CLI one-offs)
    never see the export; a secret that only exists at boot is a secret
    that's missing half the time."""
    import os

    val = os.environ.get(name, "")
    if val:
        return val
    try:
        from suijin.modules.platform.lib.config_loader import ENV_PATH

        if not ENV_PATH.is_file():
            return ""
        mtime = ENV_PATH.stat().st_mtime
        if _ENV_CACHE["mtime"] != mtime:
            data = {}
            for ln in ENV_PATH.read_text(errors="replace").splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, _, v = ln.partition("=")
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                data[k.strip()] = v
            _ENV_CACHE.update(mtime=mtime, data=data)
        return _ENV_CACHE["data"].get(name, "")
    except Exception:  # noqa: BLE001 — secret loading never breaks the KG
        return ""


_BACKEND_CACHE: dict = {}


def _invalidate_backend_cache() -> None:
    """Test hook: re-select on the next get_backend call."""
    _BACKEND_CACHE.clear()


def get_backend(json_path_fn):
    """Return the configured KG backend (config kg_backend / env override).

    Fails loud at ENGAGEMENT START, never mid-run: if neo4j is selected
    but unreachable, we fall back to JSON ONCE with an error log (a
    dead server must brick nothing), and the operator sees it in the
    journal. json (the default) always returns the file backend.
    """
    import os

    if "json" in _BACKEND_CACHE:
        return _BACKEND_CACHE["json"]  # memoized: selection reads config once
    backend = os.environ.get("SUIJIN_KG_BACKEND", "").lower()
    if not backend:
        try:
            from suijin.modules.platform.lib.config_loader import load_config

            backend = str(load_config().get("kg_backend", "json")).lower()
        except Exception:  # noqa: BLE001 — config problems never block the KG
            backend = "json"

    if backend != "neo4j":
        kg = JsonKG(json_path_fn)
        _BACKEND_CACHE["json"] = kg
        return kg

    # env > .env file > config.json (the password NEVER goes in config.json
    # — that file is committed)
    uri = _env_var("SUIJIN_NEO4J_URI")
    user = _env_var("SUIJIN_NEO4J_USER") or "neo4j"
    password = _env_var("SUIJIN_NEO4J_PASSWORD")
    if not uri:
        try:
            from suijin.modules.platform.lib.config_loader import load_config

            cfg = load_config()
            uri = str(cfg.get("neo4j_uri", ""))
            user = str(cfg.get("neo4j_user") or user)
            password = str(cfg.get("neo4j_password") or password)
        except Exception:  # noqa: BLE001
            pass
    if not uri:
        logger.error("kg_backend=neo4j but no neo4j_uri configured — staying on JSON")
        return JsonKG(json_path_fn)

    try:
        kg = Neo4jKG(uri, user, password)
        kg._connect()  # verify BEFORE the engagement starts, not mid-payload
        _BACKEND_CACHE["json"] = kg
        return kg
    except Exception as e:  # noqa: BLE001 — ImportError (no driver) or connection refused
        logger.error("Neo4j backend unavailable (%s) — staying on JSON. Fix the server and restart.", e)
        kg = JsonKG(json_path_fn)
        _BACKEND_CACHE["json"] = kg
        return kg


def _bolt_reachable(uri: str, timeout: float = 1.5) -> bool:
    """Cheap liveness probe: a TCP connect to the bolt port. No driver,
    no auth — answers 'is anything listening', which is the question a
    dead container fails."""
    import socket
    from urllib.parse import urlparse

    try:
        u = urlparse(uri if "://" in uri else f"bolt://{uri}")
        host = u.hostname or "127.0.0.1"
        port = u.port or 7687
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def backend_status() -> str:
    """Doctor line: which backend answers AND whether it's actually
    alive — a configured-but-dead neo4j must read as DOWN with the
    json fallback named, not as a green check."""
    import os

    sel = os.environ.get("SUIJIN_KG_BACKEND") or _config_backend()
    uri = os.environ.get("SUIJIN_NEO4J_URI") or _config_uri() or _config_setting("neo4j_uri")
    if sel == "neo4j":
        if not uri:
            return "neo4j (NO URI — misconfigured) [falling back to json]"
        return (
            f"neo4j ({uri}) — UP"
            if _bolt_reachable(uri)
            else f"neo4j ({uri}) — DOWN, running on json (start the server)"
        )
    return "json (default)" + (f" [neo4j configured at {uri} — flip kg_backend to switch]" if uri else "")


def _config_backend() -> str:
    try:
        from suijin.modules.platform.lib.config_loader import load_config

        return str(load_config().get("kg_backend", "json")).lower()
    except Exception:  # noqa: BLE001
        return "json"


def _config_uri() -> str:
    return _config_setting("neo4j_uri")


def _config_setting(key: str) -> str:
    try:
        from suijin.modules.platform.lib.config_loader import load_config

        return str(load_config().get(key, "") or "")
    except Exception:  # noqa: BLE001
        return ""
