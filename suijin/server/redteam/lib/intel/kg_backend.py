"""Knowledge-graph backends — JSON fallback, embedded LadybugDB, Neo4j optional.

Switch (config.json):
    "kg_backend": "ladybug",       # embedded, no Docker/server (recommended)
    "ladybug_path": "~/.suijin/intel/knowledge_graph.ladybug",  # optional
or:
    "kg_backend": "neo4j",
    "neo4j_uri":      "bolt://localhost:7687",     # or env SUIJIN_NEO4J_URI
    "neo4j_user":     "neo4j",                    # or env SUIJIN_NEO4J_USER
    "neo4j_password": "..."                        # or env SUIJIN_NEO4J_PASSWORD

Default is "json" (the existing knowledge_graph.json — unchanged,
human-readable, git-friendly). LadybugDB is the embedded graph option; it
needs no daemon and opens/closes around each operation so multiple Suijin
processes can share the file.

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

import contextlib
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


# ── LadybugDB backend (embedded, no server) ───────────────────────────────


class LadybugKG:
    """Embedded property graph (Cypher) — one file, no Docker/server.

    Ladybug holds an exclusive file lock while a Database is open, so the
    web process and agent subprocesses cannot keep a connection open at the
    same time. Each operation opens, runs and closes (with a short lock
    retry). The graph is tiny; the open cost is negligible and this keeps
    multi-process writes working without a daemon.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @contextlib.contextmanager
    def _connection(self):
        import ladybug
        import time

        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = conn = None
        last = None
        for _ in range(100):
            try:
                db = ladybug.Database(str(self._path), enable_multi_writes=True)
                conn = ladybug.Connection(db)
                break
            except Exception as exc:  # lock held by another process
                last = exc
                db = conn = None
                if "lock" not in str(exc).lower():
                    raise
                time.sleep(0.05)
        if conn is None:
            raise RuntimeError(f"LadybugDB lock unavailable: {last}")
        try:
            for ddl in (
                "CREATE NODE TABLE IF NOT EXISTS Target(name STRING, PRIMARY KEY(name))",
                "CREATE NODE TABLE IF NOT EXISTS Constraint(id STRING, rule STRING, ctype STRING, "
                "evidence STRING, confidence DOUBLE, verified_at STRING, last_seen STRING, PRIMARY KEY(id))",
                "CREATE REL TABLE IF NOT EXISTS HAS(FROM Target TO Constraint)",
            ):
                conn.execute(ddl)
            yield conn
        finally:
            with contextlib.suppress(Exception):
                conn.close()
            with contextlib.suppress(Exception):
                db.close()

    def _connect(self):
        with self._connection():
            return True

    def _rows(self, query: str, params: dict | None = None) -> list[dict]:
        with self._connection() as conn:
            result = conn.execute(query, params or {})
            columns = result.get_column_names()
            return [dict(zip(columns, row)) for row in result.get_all()]

    @staticmethod
    def _constraint_id(target: str, ctype: str, rule: str) -> str:
        import hashlib

        return hashlib.sha1(f"{target}|{ctype}|{rule}".encode()).hexdigest()[:20]

    def add_constraint(self, target, ctype, rule, evidence="", confidence=1.0) -> None:
        self._rows(
            """
            MERGE (t:Target {name: $target})
            MERGE (c:Constraint {id: $id})
            SET c.rule = $rule, c.ctype = $ctype, c.evidence = $evidence,
                c.confidence = CASE WHEN c.confidence IS NULL OR c.confidence < $confidence
                                    THEN $confidence ELSE c.confidence END,
                c.last_seen = $now,
                c.verified_at = CASE WHEN c.verified_at IS NULL THEN $now ELSE c.verified_at END
            MERGE (t)-[:HAS]->(c)
            """,
            {
                "target": str(target), "id": self._constraint_id(str(target), str(ctype), str(rule)),
                "rule": str(rule), "ctype": str(ctype), "evidence": str(evidence),
                "confidence": float(confidence), "now": _now(),
            },
        )

    def get_constraints(self, target) -> dict:
        rows = self._rows(
            """
            MATCH (t:Target {name: $target})-[:HAS]->(c:Constraint)
            RETURN c.ctype AS ctype, c.rule AS rule, c.evidence AS evidence,
                   c.confidence AS confidence, c.verified_at AS verified_at,
                   c.last_seen AS last_seen
            ORDER BY c.rule
            """,
            {"target": str(target)},
        )
        out: dict = {}
        for row in rows:
            out.setdefault(row.get("ctype") or "?", []).append({
                key: row.get(key) for key in ("rule", "evidence", "confidence", "verified_at", "last_seen")
            })
        return out

    def get_all_targets(self) -> list:
        return [row["name"] for row in self._rows(
            "MATCH (t:Target) RETURN t.name AS name ORDER BY name"
        )]

    def clear_target(self, target) -> None:
        self._rows(
            "MATCH (t:Target {name: $target})-[r:HAS]->(c:Constraint) DELETE r, c",
            {"target": str(target)},
        )
        self._rows("MATCH (t:Target {name: $target}) DELETE t", {"target": str(target)})

    def close(self) -> None:
        return None


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

    json (default) -> file backend
    ladybug        -> embedded LadybugDB file under ~/.suijin/intel
    neo4j          -> external server (legacy option)

    If a configured backend cannot open, fall back to JSON ONCE with a log
    line; a missing server or package must brick nothing.
    """
    import os

    cached = _BACKEND_CACHE.get("backend")
    if cached is not None:
        return cached
    backend = os.environ.get("SUIJIN_KG_BACKEND", "").lower()
    if not backend:
        try:
            from suijin.modules.platform.lib.config_loader import load_config

            backend = str(load_config().get("kg_backend", "json")).lower()
        except Exception:  # noqa: BLE001 — config problems never block the KG
            backend = "json"

    if backend == "ladybug":
        path = _env_var("SUIJIN_LADYBUG_PATH")
        if not path:
            try:
                from suijin.modules.platform.lib.config_loader import load_config

                path = str(load_config().get("ladybug_path", ""))
            except Exception:  # noqa: BLE001
                path = ""
        if not path:
            path = str(json_path_fn().parent / "knowledge_graph.ladybug")
        try:
            kg = LadybugKG(path)
            kg._connect()  # create/open + schema check before the run
            _BACKEND_CACHE["backend"] = kg
            return kg
        except Exception as e:  # noqa: BLE001 — ImportError or filesystem error
            logger.error("LadybugDB backend unavailable (%s) — staying on JSON.", e)
            kg = JsonKG(json_path_fn)
            _BACKEND_CACHE["backend"] = kg
            return kg

    if backend == "neo4j":
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
        if uri:
            try:
                kg = Neo4jKG(uri, user, password)
                kg._connect()
                _BACKEND_CACHE["backend"] = kg
                return kg
            except Exception as e:  # noqa: BLE001 — ImportError or connection refused
                logger.error("Neo4j backend unavailable (%s) — staying on JSON. Fix the server and restart.", e)
        else:
            logger.error("kg_backend=neo4j but no neo4j_uri configured — staying on JSON")

    kg = JsonKG(json_path_fn)
    _BACKEND_CACHE["backend"] = kg
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
    if sel == "ladybug":
        path = _env_var("SUIJIN_LADYBUG_PATH") or _config_setting("ladybug_path") or "~/.suijin/intel/knowledge_graph.ladybug"
        return f"ladybug (embedded {path}) — UP"
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
