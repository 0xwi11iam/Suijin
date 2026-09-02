"""suijin/kb.py — downloadable security knowledge base, compiled to SQLite.

`suijin pull kb` downloads pure-markdown / text knowledge bases (HackTricks,
PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP Cheat Sheets, SecLists) as
GitHub tarballs and compiles them into a single FTS5-searchable SQLite file
at suijin/kb.sqlite3. The DB never ships with the repo — users build it on
demand. The agent queries it through the `search_kb` tool (tools/intel.py).

Storage layout:
    suijin/kb_cache/    raw downloaded tarballs (skipped on re-pull)
    suijin/kb.sqlite3   compiled FTS5 database (consumed by search_kb)

Extending: add an entry to SOURCES below (repo + file patterns) — that's it.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import tarfile
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path


def _workspace_caches() -> Path:
    """The workspace caches dir (v4.1: runtime data lives in the agent
    workspace, not the package). Monkeypatch-friendly: tests may setattr
    DB_PATH / CACHE_DIR directly — this only feeds the DEFAULTS below."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    d = WORKSPACE_DIR / "caches"
    return d


DB_PATH = _workspace_caches() / "kb.sqlite3"
CACHE_DIR = _workspace_caches() / "kb_cache"

# Max bytes of a file's content indexed per document. Guards against
# single-line wordlist monsters (rockyou & co) blowing up FTS memory.
MAX_CONTENT_BYTES = 256 * 1024
# Files larger than this are skipped entirely (path-only would still be
# useful, but they are almost never worth the index cost).
MAX_FILE_BYTES = 8 * 1024 * 1024

# Download tuning — big tarballs (SecLists is ~300 MB) need room and retries.
DOWNLOAD_TIMEOUT = 600
RETRY_DELAYS = (2, 5)  # seconds before retry attempt 2 and 3
PROGRESS_EVERY = 50 * 1024 * 1024  # log a progress line every 50 MB

# ── Knowledge sources ──────────────────────────────────────────────────
# repo:     GitHub owner/name (tarball fetched from codeload, default branch)
# patterns: filename globs — matched against BOTH the repo-relative path and
#           the basename, so "_gtfobins/*" scopes to a subtree while "*.md"
#           keeps its basename meaning.
# note:     optional operator warning logged before a non-cached download.
SOURCES = {
    "payloads": {"repo": "swisskyrepo/PayloadsAllTheThings", "patterns": ["*.md"]},
    "hacktricks": {"repo": "CarlosPolop/HackTricks", "patterns": ["*.md"]},
    # GTFOBins moved its data to the GTFOBins.github.io repo: the binaries are
    # extensionless markdown files under _gtfobins/ (matched via path pattern).
    # ~20 entries are alias stubs — resolved to their target's content.
    "gtfobins": {"repo": "GTFOBins/GTFOBins.github.io", "patterns": ["_gtfobins/*"], "resolve_aliases": True},
    "lolbas": {"repo": "LOLBAS-project/LOLBAS", "patterns": ["*.md", "*.yml"]},
    "owasp": {"repo": "OWASP/CheatSheetSeries", "patterns": ["*.md"]},
    "seclists": {
        "repo": "danielmiessler/SecLists",
        "patterns": ["*.txt"],
        "note": "~300 MB download — slow on weak links",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Alias stubs (GTFOBins layout) ──────────────────────────────────────
# ~20 GTFOBins entries are one-line stubs like:
#     ---\nalias: mawk\n...\n
# pointing at the canonical binary's page. Indexing the stub as-is would make
# e.g. "awk sudo" unfindable, so we resolve stubs to the target's content.
_ALIAS_STUB_RE = re.compile(r"\A---\s*\nalias:\s*(\S+)\s*\n\.\.\.\s*\n?\Z")


def _resolve_alias_stubs(docs: list[tuple]) -> list[tuple]:
    by_name = {Path(path).name: content for _src, path, _title, content in docs}
    resolved = []
    for src, path, title, content in docs:
        m = _ALIAS_STUB_RE.match(content)
        if m and m.group(1) in by_name:
            content = by_name[m.group(1)] + f"\n\n[alias of {m.group(1)}]"
        resolved.append((src, path, title, content))
    return resolved


# ── Download ───────────────────────────────────────────────────────────


def fetch_source(name: str, force: bool = False, cache_dir: Path | None = None, session=None, log=print) -> Path:
    """Download a source's tarball into the cache; returns the tarball path.

    Uses HEAD (default branch) first, falling back to master/main. Each ref
    gets up to 3 attempts (RETRY_DELAYS backoff). Partial downloads are
    never resumed — a stale .part is deleted before and after every attempt.
    Cached tarballs are reused unless force=True.
    """
    if name not in SOURCES:
        raise ValueError(f"Unknown KB source '{name}'. Available: {', '.join(sorted(SOURCES))}")
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    tar_path = cache / f"{name}.tar.gz"
    if tar_path.exists() and tar_path.stat().st_size > 0 and not force:
        log(f"[kb] {name}: using cached tarball ({tar_path.stat().st_size // 1024} KB)")
        return tar_path

    stale_part = tar_path.with_suffix(".part")
    if not force and stale_part.exists():
        log(f"[kb] {name}: discarding stale partial download ({stale_part.stat().st_size // (1024 * 1024)} MB)")

    req = session or _default_session()
    repo = SOURCES[name]["repo"]
    note = SOURCES[name].get("note")
    if note:
        log(f"[kb] {name}: large source — {note}")
    refs = ["HEAD", "master", "main"]
    last_err = None
    for ref in refs:
        url = f"https://codeload.github.com/{repo}/tar.gz/{ref}"
        for attempt, delay in enumerate((0,) + RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            tmp = tar_path.with_suffix(".part")
            try:
                resp = req.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code} for {ref}"
                    if resp.status_code in (404, 410):
                        break  # wrong ref — retrying won't help
                    continue  # transient — retry same ref
                tmp.unlink(missing_ok=True)  # never resume a stale partial
                done = 0
                next_log = PROGRESS_EVERY
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if done >= next_log:
                            log(f"[kb] {name}: {done // (1024 * 1024)} MB downloaded ...")
                            next_log += PROGRESS_EVERY
                if done == 0:
                    last_err = f"empty download for {ref}"
                    tmp.unlink(missing_ok=True)
                    continue
                tmp.replace(tar_path)
                log(f"[kb] {name}: downloaded {done // 1024} KB ({repo}@{ref}, attempt {attempt + 1})")
                return tar_path
            except Exception as e:  # network errors — retry, then next ref
                last_err = str(e)
                tmp.unlink(missing_ok=True)
    # No ref worked; make sure no half-written tarball/part survives
    tar_path.with_suffix(".part").unlink(missing_ok=True)
    raise RuntimeError(f"[kb] {name}: failed to download {repo} ({last_err})")


def _default_session():
    from suijin.modules.platform.lib.runtime import global_session

    return global_session


# ── Extract ────────────────────────────────────────────────────────────


def _title_from(content: str, fallback: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("# ").strip()[:120] or fallback
    return fallback


def iter_docs(tar_path: Path, source: str, patterns: list[str]):
    """Yield (source, path, title, content) for every matching file in a tarball.

    Patterns match against BOTH the repo-relative path and the basename, so
    "_gtfobins/*" scopes to a subtree while "*.md" keeps basename semantics.
    Content is capped at MAX_CONTENT_BYTES; files over MAX_FILE_BYTES are
    skipped entirely.
    """
    with tarfile.open(tar_path, mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.size > MAX_FILE_BYTES:
                continue
            # Strip the leading "{repo}-{sha}/" directory GitHub adds
            parts = member.name.split("/", 1)
            rel_path = parts[1] if len(parts) == 2 else member.name
            base = Path(rel_path).name
            if not any(fnmatch(base, p) or fnmatch(rel_path, p) for p in patterns):
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            raw = f.read(MAX_CONTENT_BYTES + 1)
            truncated = len(raw) > MAX_CONTENT_BYTES
            content = raw[:MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
            if truncated:
                content += "\n... [truncated]"
            yield source, rel_path, _title_from(content, base), content


# ── Compile ────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE kb_files (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL
);
CREATE INDEX idx_kb_source ON kb_files(source);
CREATE TABLE kb_meta (key TEXT PRIMARY KEY, value TEXT);
"""

_SCHEMA_FTS = """
CREATE VIRTUAL TABLE kb_fts USING fts5(
    path, title, content,
    content='kb_files', content_rowid='id',
    tokenize='porter unicode61'
);
"""


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def compile_kb(
    sources: list[str] | None = None,
    force: bool = False,
    db_path: Path | None = None,
    cache_dir: Path | None = None,
    session=None,
    log=print,
) -> dict:
    """Download (as needed) and compile sources into a fresh FTS5 database.

    Builds into a temp file and atomically replaces the target DB. Returns
    a summary dict: {source: doc_count, ..., "_total": n, "_fts5": bool}.
    """
    names = list(sources) if sources else list(SOURCES)
    for n in names:
        if n not in SOURCES:
            raise ValueError(f"Unknown KB source '{n}'. Available: {', '.join(sorted(SOURCES))}")

    target = Path(db_path) if db_path else DB_PATH
    tmp = target.with_suffix(".sqlite3.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    summary: dict = {}
    failures: dict = {}
    total = 0
    use_fts = False
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(_SCHEMA)
        use_fts = _fts5_available(conn)
        if use_fts:
            conn.executescript(_SCHEMA_FTS)

        for name in names:
            try:
                tar_path = fetch_source(name, force=force, cache_dir=cache_dir, session=session, log=log)
                count = 0
                if SOURCES[name].get("resolve_aliases"):
                    # Small, stub-heavy sources: buffer the whole source so
                    # alias stubs can be resolved to their target's content.
                    batch = _resolve_alias_stubs(list(iter_docs(tar_path, name, SOURCES[name]["patterns"])))
                    count += _flush(conn, batch, use_fts)
                else:
                    batch = []
                    for doc in iter_docs(tar_path, name, SOURCES[name]["patterns"]):
                        batch.append(doc)
                        if len(batch) >= 500:
                            count += _flush(conn, batch, use_fts)
                            batch = []
                    count += _flush(conn, batch, use_fts)
                if count == 0:
                    # A "successful" download that indexes nothing is a bug
                    # (dead repo moved, layout changed) — surface it as a
                    # failure instead of silently shipping an empty source.
                    raise RuntimeError(
                        "downloaded OK but 0 files matched — patterns likely "
                        f"wrong for this repo layout: {SOURCES[name]['patterns']}"
                    )
                summary[name] = count
                total += count
                log(f"[kb] {name}: indexed {count:,} docs")
            except Exception as e:
                # One dead source must not kill the whole pull — skip it,
                # record the failure, keep building the rest.
                failures[name] = str(e)
                log(f"[kb] {name}: FAILED ({e}) — continuing")

        if not summary and failures:
            conn.close()
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"all sources failed: {failures}")

        conn.executemany(
            "INSERT OR REPLACE INTO kb_meta(key, value) VALUES (?, ?)",
            [(f"count:{k}", str(v)) for k, v in summary.items()]
            + [
                ("_total", str(total)),
                ("_built_at", _utc_now()),
                ("_fts5", "1" if use_fts else "0"),
                ("_sources", json.dumps(names)),
                ("_failed", json.dumps(failures)),
            ],
        )
        conn.commit()
        conn.close()
        tmp.replace(target)
        log(f"[kb] compiled {total:,} docs -> {target} (FTS5: {'yes' if use_fts else 'no, LIKE fallback'})")
        summary["_total"] = total
        summary["_fts5"] = use_fts
        if failures:
            summary["_failed"] = failures
        return summary
    except Exception:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise


def _flush(conn: sqlite3.Connection, batch: list, use_fts: bool) -> int:
    if not batch:
        return 0
    conn.executemany(
        "INSERT INTO kb_files(source, path, title, content) VALUES (?, ?, ?, ?)",
        batch,
    )
    if use_fts:
        conn.execute("INSERT INTO kb_fts(kb_fts) VALUES('rebuild')")
    return len(batch)


# ── Status ─────────────────────────────────────────────────────────────


def kb_status(db_path: Path | None = None) -> dict | None:
    """Return build status of the KB, or None if not built.

    Counts only sources that actually indexed documents (per the count:*
    meta rows), so a partially failed build is reported honestly:
      {'docs': n, 'sources': n, 'per_source': {name: count},
       'failed': {name: err}, 'built_at': iso, 'age_days': n|None,
       'fts5': bool, 'size_bytes': n}
    """
    target = Path(db_path) if db_path else DB_PATH
    if not target.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            total = conn.execute("SELECT value FROM kb_meta WHERE key='_total'").fetchone()
            built = conn.execute("SELECT value FROM kb_meta WHERE key='_built_at'").fetchone()
            if not total:
                return None
            per_source = {
                k.split(":", 1)[1]: int(v)
                for k, v in conn.execute("SELECT key, value FROM kb_meta WHERE key LIKE 'count:%'")
            }
            failed_row = conn.execute("SELECT value FROM kb_meta WHERE key='_failed'").fetchone()
            fts_row = conn.execute("SELECT value FROM kb_meta WHERE key='_fts5'").fetchone()
            built_at = built[0] if built else "?"
            age_days = None
            with contextlib.suppress(ValueError):
                age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(built_at)).days
            return {
                "docs": int(total[0]),
                "sources": len(per_source),
                "per_source": per_source,
                "failed": json.loads(failed_row[0]) if failed_row and failed_row[0] else {},
                "built_at": built_at,
                "age_days": age_days,
                "fts5": bool(fts_row and fts_row[0] == "1"),
                "size_bytes": target.stat().st_size,
            }
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None


# ── Full-document reads (the index copy may be truncated) ─────────────


def read_doc(path: str, source: str | None = None, cache_dir: Path | None = None) -> tuple[str, str, str]:
    """Return (source, path, full_content) for a KB doc, read from its tarball.

    The FTS index caps content at MAX_CONTENT_BYTES; this pulls the original
    file so the agent can read an entire cheatsheet. `path` may be a unique
    substring (e.g. "sql-injection/README.md"); source is inferred when
    omitted. Raises FileNotFoundError when nothing matches, ValueError when
    ambiguous.
    """
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    needle = (path or "").strip().strip("/")
    if not needle:
        raise ValueError("path required (e.g. '_gtfobins/awk' or 'sqli.md')")
    all_members: list[tuple[str, str, Path, object]] = []
    names = [source] if source else list(SOURCES)
    for name in names:
        if name not in SOURCES:
            raise ValueError(f"Unknown KB source '{name}'. Available: {', '.join(sorted(SOURCES))}")
        tar_path = cache / f"{name}.tar.gz"
        if not tar_path.exists():
            continue
        with tarfile.open(tar_path, mode="r:gz") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                parts = member.name.split("/", 1)
                rel = parts[1] if len(parts) == 2 else member.name
                all_members.append((name, rel, tar_path, member))

    # tier 1: exact path (case-insensitive) — the strongest signal
    needle_low = needle.lower()
    candidates = [(s, r, t, m) for s, r, t, m in all_members if r.lower() == needle_low]
    # tier 2: suffix match — path components align from the end,
    # extension-insensitive (shared_name matches shared_name.md)
    if not candidates:
        needle_parts = needle_low.split("/")

        def _stem(path: str) -> str:
            from os.path import splitext

            return splitext(path)[0].lower()

        candidates = [
            (s, r, t, m)
            for s, r, t, m in all_members
            if "/".join(r.lower().split("/")[-len(needle_parts) :]) == "/".join(needle_parts) or _stem(r) == needle_low
        ]
    # tier 3: prefix — starts with the needle (stronger than substring)
    if not candidates:
        candidates = [(s, r, t, m) for s, r, t, m in all_members if r.lower().startswith(needle_low)]
    # tier 4: substring — LAST resort (this is where 'SQL Injection'
    # used to match 'NoSQL Injection/README.md' before its own doc)
    if not candidates:
        candidates = [(s, r, t, m) for s, r, t, m in all_members if needle_low in r.lower()]
    if not candidates:
        raise FileNotFoundError(
            f"No KB file matches '{needle}' in cached tarballs "
            f"(sources cached: {', '.join(sorted(p.name for p in cache.glob('*.tar.gz'))) or 'none'}). "
            "Run: suijin pull kb"
        )
    if len(candidates) > 1 and not source:
        listing = ", ".join(f"{s}:{r}" for s, r, _, _ in candidates[:6])
        raise ValueError(f"Ambiguous path '{needle}' — matches {len(candidates)} docs: {listing}")
    name, rel, tar_path, member = candidates[0]
    with tarfile.open(tar_path, mode="r:gz") as tar:
        f = tar.extractfile(member)
        content = f.read().decode("utf-8", errors="ignore") if f else ""
    return name, rel, content


def kb_diff(db_path: Path | None = None, cache_dir: Path | None = None) -> dict:
    """Compare the built DB against its cached tarballs.

    Reports, per source: indexed doc count vs current cache, whether the
    cached tarball is NEWER than the last build (re-pull would change the
    index), and sources that have a cache but no index (or vice versa).
    """
    from datetime import datetime as _dt

    target = Path(db_path) if db_path else DB_PATH
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    st = kb_status(target)
    built_at = None
    if st:
        with contextlib.suppress(ValueError):
            built_at = _dt.fromisoformat(st["built_at"])
    rows: dict[str, dict] = {}
    for name in SOURCES:
        tar_path = cache / f"{name}.tar.gz"
        cached = tar_path.exists()
        indexed = (st.get("per_source", {}) or {}).get(name) if st else None
        newer = bool(cached and built_at and _dt.fromtimestamp(tar_path.stat().st_mtime, tz=timezone.utc) > built_at)
        rows[name] = {
            "cached": cached,
            "indexed_docs": indexed,
            "cache_newer_than_build": newer,
            "action": (
                "pull"
                if cached and indexed is None
                else "rebuild"
                if newer
                else "cache missing"
                if indexed is not None and not cached
                else "ok"
            ),
        }
    return {"built": bool(st), "built_at": st.get("built_at") if st else None, "sources": rows}


if __name__ == "__main__":  # manual build: python -m suijin.modules.knowledge.lib.kb
    compile_kb()
