"""The local data hub — every piece of Suijin data, through one door.

Suijin keeps its state in a handful of places: a sqlite swarm database, one folder per
engagement, the config directory, the knowledge graph, the mesh bus. Each has its own reader and
most of them are private to the console. The hub puts a registry in front of all of them, so a
plugin, a script or the console UI asks the same three questions of any store::

    from suijin.server.web import data_hub

    data_hub.catalog()                       # every store, with live counts and freshness
    data_hub.read("swarm.agents")            # rows, as plain dicts
    data_hub.read("engagement.<slug>.events", kind="tool.call", limit=50)
    data_hub.watch(callback)                 # "swarm.intents changed" — in-process and on the bus

Rules the hub keeps, in order:

1. **Read-only.** Nothing here writes a store. Control stays with the console's routes, which
   go through guardrails; the hub is the reading side of the data flow.
2. **Secrets never leave the process.** SECRET stores expose names and counts only, and
   secret-bearing keys inside operator stores are masked before anything is returned.
3. **No machine-specific paths.** Locations are relative to the runtime root or the repo.
4. **Never fatal.** A store that cannot be read right now reports why and returns no rows; the
   console carries on.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MASK = "••••••"

# ── redaction ─────────────────────────────────────────────────────────────────────────────

# A key is a secret when its whole name, or its last `_`/`-` separated part, is a secret word:
# `password`, `neo4j_password`, `custom_providers[].api_key`, `OPENAI_API_KEY`. Words that merely
# contain "key" or "token" are settings — `max_tokens`, `token_count`, `keyEnv`, `keyboard`.
_SECRET_KEY = re.compile(
    r"^(?:.*[_-])?(?:api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|id[-_]?token|auth[-_]?token|token|"
    r"secret|client[-_]?secret|secret[-_]?key|password|passwd|pwd|credential|credentials|authorization|"
    r"proxy[-_]?authorization|cookie|cookies|set[-_]?cookie|bearer|private[-_]?key|session[-_]?key|"
    r"extraheaders|identity[-_]?headers)$",
    re.I,
)
_SECRET_ENV_KEY = re.compile(r"^[A-Z0-9_]*(_KEY|_TOKEN|_SECRET|_PASSWORD|_PWD)$")


def _is_secret_key(key: str) -> bool:
    name = str(key)
    if _SECRET_KEY.search(name) or _SECRET_ENV_KEY.match(name):
        return True
    # A bare lowercase `key` is a name (a registry key such as `zai`); `KEY` or `Key` is not.
    return name.lower() == "key" and not name.islower()


_SECRET_TEXT = (
    (re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9\-_=+.\/]{8,}"), r"\1 " + MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), MASK),
    (
        re.compile(
            r"\b((?:x-)?api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|client[-_]?secret|"
            r"secret|password|passwd|authorization)([\"']?\s*[:=]\s*[\"']?)(?!(?:Bearer|Basic|Token)\b)([^\s,\"'}&\]]{6,})",
            re.I,
        ),
        r"\1\2" + MASK,
    ),
)


def mask_text(text: str) -> str:
    """The secrets inside one string: bearer and basic headers, JWTs, `key=value` credentials."""
    out = str(text)
    for pattern, replacement in _SECRET_TEXT:
        out = pattern.sub(replacement, out)
    if re.search(r"\bCookie:\s*", out, re.I):
        out = re.sub(
            r"(Cookie:\s*)([^\n]+)",
            lambda m: (
                m.group(1)
                + "; ".join(
                    f"{part.split('=', 1)[0]}={MASK}" if "=" in part else part for part in re.split(r";\s*", m.group(2))
                )
            ),
            out,
            flags=re.I,
        )
    return out


def redact(value, key: str = "", _seen: frozenset[int] = frozenset()):
    """A copy of ``value`` with credential-shaped keys masked wherever they nest, and the secrets
    inside strings masked. Settings that merely contain "token" or "key" — ``max_tokens``,
    ``model_key``, ``keyEnv`` — are left alone."""
    if isinstance(value, str):
        return MASK if _is_secret_key(key) else mask_text(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if not isinstance(value, (dict, list)) or id(value) in _seen:
        return value
    seen = _seen | {id(value)}
    if isinstance(value, list):
        return [redact(item, key, seen) for item in value]
    return {str(name): redact(item, str(name), seen) for name, item in value.items()}


# ── the registry ──────────────────────────────────────────────────────────────────────────

PUBLIC, OPERATOR, SECRET = "public", "operator", "secret"


@dataclass
class Store:
    """One place Suijin keeps data."""

    id: str
    group: str
    label: str
    kind: str  # sqlite-table | json | jsonl | directory | markdown | text | registry | derived
    where: str  # relative to the runtime root or the repo — never absolute
    description: str
    sensitivity: str = OPERATOR
    columns: list[str] = field(default_factory=list)
    filterable: list[str] = field(default_factory=list)
    # ``reader(offset, limit, query, **filters)`` → rows (list[dict]); ``counter()`` → rows on disk
    reader: Callable[..., list] | None = None
    counter: Callable[[], int] | None = None
    volatile: bool = True  # changes while Suijin runs

    def __post_init__(self) -> None:
        # A reader built by this module carries its own counter; a store declared without one
        # would never report a row count or a change.
        if self.counter is None and self.reader is not None and hasattr(self.reader, "counter"):
            self.counter = self.reader.counter

    def live_columns(self) -> list[str]:
        """The columns that exist right now (a table gains columns as Suijin grows)."""
        if self.reader is not None and hasattr(self.reader, "columns"):
            return list(self.reader.columns())
        return list(self.columns)

    def describe(self) -> dict:
        try:
            rows = self.counter() if self.counter else None
        except Exception:  # noqa: BLE001 — a store that cannot be counted still gets described
            rows = None
        return {
            "id": self.id,
            "group": self.group,
            "label": self.label,
            "kind": self.kind,
            "where": self.where,
            "description": self.description,
            "sensitivity": self.sensitivity,
            "columns": self.live_columns(),
            "filterable": self.filterable,
            "volatile": self.volatile,
            "rows": rows,
        }


_STORES: dict[str, Store] = {}
_LOCK = threading.RLock()


def register(store: Store) -> Store:
    with _LOCK:
        _STORES[store.id] = store
    return store


def unregister(prefix: str) -> None:
    with _LOCK:
        for key in [key for key in _STORES if key == prefix or key.startswith(prefix + ".")]:
            _STORES.pop(key, None)


def get(store_id: str) -> Store | None:
    with _LOCK:
        return _STORES.get(store_id)


# ── safe readers ───────────────────────────────────────────────────────────────────────────


def _connect(path: Path, timeout: float = 2.0) -> sqlite3.Connection | None:
    """A short-timeout, read-only connection; None when the file or its driver is unavailable."""
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def sqlite_reader(path: Callable[[], Path], table: str, order: str = "rowid DESC"):
    """Rows of one table, newest first, with only the columns that exist on this install."""

    def reader(offset: int = 0, limit: int = 100, query: str | None = None, **filters) -> list[dict]:
        conn = _connect(path())
        if conn is None:
            return []
        try:
            cols = _columns(conn, table)
            if not cols:
                return []
            where, args = [], []
            if query:
                clause = " OR ".join(f"CAST({col} AS TEXT) LIKE ?" for col in cols)
                where.append(f"({clause})")
                args.extend([f"%{query}%"] * len(cols))
            for name, value in filters.items():
                if value not in (None, "") and name in cols:
                    where.append(f"CAST({name} AS TEXT) = ?")
                    args.append(str(value))
            sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(where) if where else "")
            sql += f" ORDER BY {order} LIMIT ? OFFSET ?" if order else " LIMIT ? OFFSET ?"
            return [redact(dict(row)) for row in conn.execute(sql, [*args, int(limit), int(offset)]).fetchall()]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def counter() -> int:
        conn = _connect(path())
        if conn is None:
            return 0
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def columns() -> list[str]:
        conn = _connect(path())
        if conn is None:
            return []
        try:
            return _columns(conn, table)
        finally:
            conn.close()

    reader.columns = columns  # type: ignore[attr-defined]
    reader.counter = counter  # type: ignore[attr-defined]
    return reader


def json_reader(path: Callable[[], Path], pick: Callable[[dict], list[dict]] | None = None, ttl: float = 5.0):
    """Rows out of a JSON file. ``pick`` turns the document into rows; the default is one row per
    top-level key — a config file becomes a setting table. Each value is judged by its setting's
    NAME, so ``neo4j_password`` is masked however deeply it sits, while the row's own field names
    are never mistaken for secrets. ``ttl`` is kept for call-site compatibility; the cache follows
    the file itself."""
    cache: dict = {"stamp": ..., "doc": None}

    def load():
        """Re-read only when the file changed (its size or modification time), so a file written a
        moment ago is seen at once and an untouched one is never read twice."""
        file = path()
        try:
            stat = file.stat() if file.is_file() else None
        except OSError:
            stat = None
        stamp = (stat.st_mtime_ns, stat.st_size) if stat else None
        if cache["stamp"] == stamp:
            return cache["doc"]
        doc = None
        if stat is not None:
            with contextlib.suppress(Exception):
                doc = json.loads(file.read_text(errors="replace"))
        cache.update(stamp=stamp, doc=doc)
        return doc

    def rows() -> list[dict]:
        doc = load()
        if doc is None:
            return []
        if pick:
            return redact(pick(doc))
        return (
            [{"setting": str(key), "value": redact(value, str(key))} for key, value in doc.items()]
            if isinstance(doc, dict)
            else []
        )

    def reader(offset: int = 0, limit: int = 100, query: str | None = None, **_filters) -> list[dict]:
        found = rows()
        if query:
            needle = query.lower()
            found = [row for row in found if needle in json.dumps(row, default=str).lower()]
        return found[int(offset) : int(offset) + int(limit)]

    def counter() -> int:
        return len(rows())

    reader.counter = counter  # type: ignore[attr-defined]
    return reader


def jsonl_reader(path: Callable[[], Path], kinds: Callable[[list[dict]], list[str]] | None = None):
    """Rows out of a JSON Lines file, newest first. Reads the tail, so a 4,000-line journal costs
    one pass; ``kind`` filters by the record's kind."""

    def rows() -> list[dict]:
        file = path()
        if not file.is_file():
            return []
        out = []
        with contextlib.suppress(OSError):
            for line in file.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                with contextlib.suppress(ValueError):
                    record = json.loads(line)
                    if isinstance(record, dict):
                        out.append(record)
        return out

    def reader(
        offset: int = 0, limit: int = 200, query: str | None = None, kind: str | None = None, **_filters
    ) -> list[dict]:
        found = rows()
        if kind:
            found = [record for record in found if str(record.get("kind") or "") == kind]
        if query:
            needle = query.lower()
            found = [record for record in found if needle in json.dumps(record, default=str).lower()]
        found.reverse()
        return redact(found[int(offset) : int(offset) + int(limit)])

    def counter() -> int:
        return len(rows())

    def kinds() -> list[str]:
        return sorted({str(record.get("kind")) for record in rows() if record.get("kind")})

    reader.kinds = kinds  # type: ignore[attr-defined]
    reader.counter = counter  # type: ignore[attr-defined]
    return reader


def directory_reader(
    root: Callable[[], Path], pattern: str = "*", tail: int | None = 4000, exclude: frozenset[str] = frozenset()
):
    """The files of a directory as rows (name, bytes, modified); ``?name=`` returns one file's
    text content (text files only, capped). ``exclude`` names top-level folders that are neither
    listed nor readable — the agent's userland, for instance."""

    def listing() -> list[Path]:
        base = root()
        if not base.is_dir():
            return []
        with contextlib.suppress(OSError):
            return sorted(
                (p for p in base.rglob(pattern) if p.is_file() and not exclude & set(p.relative_to(base).parts[:-1])),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        return []

    def reader(
        offset: int = 0, limit: int = 100, query: str | None = None, name: str | None = None, **_filters
    ) -> list[dict]:
        if name:
            base = root().resolve()
            target = (base / name).resolve()
            inside = base in target.parents and not exclude & set(target.relative_to(base).parts[:-1])
            if not inside or not target.is_file():
                return [{"error": "not found"}]
            try:
                body = target.read_bytes()
            except OSError as error:
                return [{"error": f"{error.strerror or error}"}]
            if b"\x00" in body[:2000]:
                return [{"name": name, "bytes": len(body), "binary": True}]
            text = body.decode("utf-8", errors="replace")
            return [
                {
                    "name": name,
                    "bytes": len(body),
                    "truncated": len(text) > tail,
                    "content": mask_text(text[:tail] if tail else text),
                }
            ]
        rows = [
            {
                "name": str(path.relative_to(root())),
                "bytes": path.stat().st_size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)),
            }
            for path in listing()
        ]
        if query:
            rows = [row for row in rows if query.lower() in row["name"].lower()]
        return rows[int(offset) : int(offset) + int(limit)]

    def counter() -> int:
        return len(listing())

    reader.counter = counter  # type: ignore[attr-defined]
    return reader


def registry_reader(loader: Callable[[], list[dict]], ttl: float = 5.0):
    """Rows out of a function — an in-repo registry such as the tool routes. The loader runs at
    most once per ``ttl`` seconds; the catalog is polled, and a loader can cost seconds."""
    cache: dict = {"at": 0.0, "rows": []}

    def load() -> list[dict]:
        if time.time() - cache["at"] >= ttl:
            with contextlib.suppress(Exception):
                cache.update(at=time.time(), rows=loader())
        return cache["rows"]

    def reader(offset: int = 0, limit: int = 500, query: str | None = None, **_filters) -> list[dict]:
        with contextlib.suppress(Exception):
            rows = load()
            if query:
                needle = query.lower()
                rows = [row for row in rows if needle in json.dumps(row, default=str).lower()]
            return redact(rows[int(offset) : int(offset) + int(limit)])
        return []

    def counter() -> int:
        return len(load())

    reader.counter = counter  # type: ignore[attr-defined]
    return reader


# ── catalog / read ─────────────────────────────────────────────────────────────────────────


def _fingerprint(store: Store) -> tuple:
    """What "changed" means for a store: its row count. Cheap, and enough to notify on."""
    try:
        return (store.counter() if store.counter else None,)
    except Exception:  # noqa: BLE001
        return (None,)


def catalog(group: str | None = None) -> list[dict]:
    """Every store with its live row count. Engagement stores are refreshed first."""
    refresh_engagements()
    with _LOCK:
        stores = sorted(_STORES.values(), key=lambda store: (store.group, store.id))
    if group:
        stores = [store for store in stores if store.group == group]
    return [store.describe() for store in stores]


def read(store_id: str, offset: int = 0, limit: int = 100, query: str | None = None, **filters) -> dict:
    """Rows of one store. ``query`` is a free-text filter across all columns; other keyword
    arguments filter exact columns (``kind``, ``name``, ``swarm`` …)."""
    refresh_engagements()
    store = get(store_id)
    if store is None:
        return {"store": store_id, "error": "no such store", "rows": []}
    if store.sensitivity == SECRET and not store.reader:
        return {"store": store_id, "error": "this store is secret: only its name and size are exposed", "rows": []}
    if store.reader is None:
        return {"store": store_id, "error": "this store cannot be read as rows", "rows": []}
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    try:
        rows = store.reader(offset, limit, query, **filters)
    except Exception as error:  # noqa: BLE001 — never fatal
        return {"store": store_id, "error": f"{type(error).__name__}: {str(error)[:160]}", "rows": []}
    columns = sorted({key for row in rows[:200] for key in row})
    return {
        "store": store_id,
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "total": store.counter() if store.counter else None,
        "columns": columns,
    }


def kinds(store_id: str) -> list[dict]:
    """The distinct record kinds of a journal, each with how many rows carry it."""
    store = get(store_id)
    if store is None or store.reader is None or not hasattr(store.reader, "kinds"):
        return []
    counts: dict[str, int] = {}
    for row in store.reader(0, 1_000_000_000, None):
        kind = str(row.get("kind"))
        counts[kind] = counts.get(kind, 0) + 1
    return [{"kind": kind, "rows": counts.get(kind, 0)} for kind in store.reader.kinds()]


# ── watch ──────────────────────────────────────────────────────────────────────────────────

_WATCHERS: list[Callable[[str, dict], None]] = []
_WATCH_STATE: dict[str, tuple] = {}
_WATCH_THREAD: threading.Thread | None = None
_WATCH_STOP = threading.Event()
WATCH_INTERVAL_S = 2.0


def publish_change(store_id: str, detail: dict | None = None) -> None:
    """Tell every watcher (and the mesh bus) that a store changed."""
    payload = {"store": store_id, "at": time.time(), **(detail or {})}
    for watcher in list(_WATCHERS):
        with contextlib.suppress(Exception):
            watcher(store_id, payload)
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient, discover_bus

        addr = discover_bus()
        if addr:
            client = BusClient()
            try:
                if client.connect(addr[0], addr[1], f"data-hub-{os.getpid()}"):
                    client.publish("/data/change", payload)
            finally:
                client.close()


def _watch_loop() -> None:
    while not _WATCH_STOP.wait(WATCH_INTERVAL_S):
        with _LOCK:
            stores = list(_STORES.values())
        for store in stores:
            if not store.volatile or store.counter is None:
                continue
            mark = _fingerprint(store)
            previous = _WATCH_STATE.get(store.id)
            if previous is not None and previous != mark:
                publish_change(store.id, {"rows": mark[0]})
            _WATCH_STATE[store.id] = mark


def watch(callback: Callable[[str, dict], None]) -> Callable[[], None]:
    """Call ``callback(store_id, payload)`` whenever a store changes. Returns an unwatch function.
    The first pass only records the baseline, so nothing fires for the state as it is found."""
    global _WATCH_THREAD
    with _LOCK:
        _WATCHERS.append(callback)
        for store in _STORES.values():
            if store.volatile and store.counter is not None:
                _WATCH_STATE[store.id] = _fingerprint(store)
        if _WATCH_THREAD is None or not _WATCH_THREAD.is_alive():
            _WATCH_STOP.clear()
            _WATCH_THREAD = threading.Thread(target=_watch_loop, daemon=True, name="data-hub-watch")
            _WATCH_THREAD.start()

    def unwatch() -> None:
        with _LOCK:
            if callback in _WATCHERS:
                _WATCHERS.remove(callback)

    return unwatch


# The stores themselves are declared in data_stores.py (imported at the end so the registry
# helpers above exist first).
from suijin.server.web import data_stores as _data_stores  # noqa: E402


def refresh_engagements() -> None:
    """Engagements come and go: register a store per engagement folder."""
    with contextlib.suppress(Exception):
        _data_stores.register_engagements()


__all__ = [
    "PUBLIC",
    "OPERATOR",
    "SECRET",
    "Store",
    "catalog",
    "read",
    "kinds",
    "watch",
    "publish_change",
    "register",
    "unregister",
    "get",
    "redact",
    "mask_text",
    "refresh_engagements",
]
