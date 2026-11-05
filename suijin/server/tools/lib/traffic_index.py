"""Traffic index — every request searchable (wall memo slice 3).

ARTEX's recording proxy, adapted to suijin's constraint: flows land in
SQLite with FTS on bodies; large bodies spill to content-addressed
blobs. traffic_search/traffic_get/traffic_blob answer from it. The
mitmproxy capture module feeds it; the observation detectors will run
on it (not only on tools that remember to record).
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchange (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, method TEXT, url TEXT, host TEXT, path TEXT,
    status INTEGER, req_headers TEXT, res_headers TEXT,
    req_body TEXT, res_body TEXT, res_blob TEXT, size INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS exchange_fts USING fts5(
    url, req_body, res_body, content='exchange', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS exchange_ai AFTER INSERT ON exchange BEGIN
    INSERT INTO exchange_fts(rowid, url, req_body, res_body)
    VALUES (new.id, new.url, new.req_body, new.res_body);
END;
"""


def traffic_db() -> Path:
    from suijin.modules.platform.lib.workspace import state_dir

    d = state_dir() / "traffic"
    d.mkdir(parents=True, exist_ok=True)
    return d / "index.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(traffic_db()), timeout=10)
    c.executescript(_SCHEMA)
    return c


def record_exchange(
    method: str, url: str, status: int, req_headers: str = "", res_headers: str = "", req_body: str = "", res_body: str = ""
) -> int:
    """Insert one exchange; large bodies spill to content-addressed
    blobs (the row carries the hash; traffic_blob pages it back)."""
    import urllib.parse

    u = urllib.parse.urlparse(str(url or "http://x/"))
    blob = ""
    if len(res_body) > 100_000:
        h = hashlib.sha256(res_body.encode(errors="replace")).hexdigest()[:16]
        bdir = traffic_db().parent / "blobs"
        bdir.mkdir(exist_ok=True)
        (bdir / h).write_text(res_body, encoding="utf-8", errors="replace")
        blob, res_body = h, ""
    with contextlib.suppress(sqlite3.Error):
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO exchange (ts, method, url, host, path, status, req_headers, res_headers, req_body, res_body, res_blob, size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), str(method)[:8], str(url)[:2000], u.netloc[:200], u.path[:500],
                    int(status or 0), str(req_headers)[:4000], str(res_headers)[:4000],
                    str(req_body)[:100_000], str(res_body)[:100_000], blob, len(str(res_body)),
                ),
            )
            return int(cur.lastrowid or 0)
    return 0


def traffic_search(host: str = "", body: str = "", status: int = 0, limit: int = 3) -> str:
    """Thin rows by default (ARTEX rule: the model asks for more only
    when it needs more). host is REQUIRED — a search across everything
    is noise."""
    if not str(host or "").strip():
        return "Error: host required (searching all hosts is noise)"
    q, params = "SELECT id, method, url, status, size FROM exchange WHERE host LIKE ?", (f"%{host}%",)
    if status:
        q, params = q + " AND status=?", (*params, int(status))
    if body:
        q = (
            "SELECT e.id, e.method, e.url, e.status, e.size FROM exchange e "
            "JOIN exchange_fts f ON e.id=f.rowid WHERE e.host LIKE ? AND exchange_fts MATCH ?"
        )
        params = (f"%{host}%", f'"{body}"')
    # ORDER BY e.id requires the e alias — the no-body query has none
    # (the error was suppressed into a silent 'no exchanges' lie)
    if body:
        q += f" ORDER BY e.id DESC LIMIT {max(1, min(int(limit or 3), 50))}"
    else:
        q += f" ORDER BY id DESC LIMIT {max(1, min(int(limit or 3), 50))}"
    found = []
    with contextlib.suppress(sqlite3.Error), _conn() as c:
        found = c.execute(q, params).fetchall()
    found = found or []
    if not found:
        return f"no exchanges for host={host}" + (f" body~{body!r}" if body else "")
    out = [f"traffic: {len(found)} row(s) for {host}" + (f" body~{body!r}" if body else "")]
    out += [f"  #{i} [{st}] {m} {u[:80]} ({sz}B)" for i, m, u, st, sz in found]
    out.append("traffic_get(id=N) for the exchange")
    return "\n".join(out)


def traffic_get(exchange_id: int) -> str:
    """One full exchange: request headers, response headers, bodies
    (truncated when a blob holds the rest)."""
    with contextlib.suppress(sqlite3.Error), _conn() as c:
        r = c.execute(
            "SELECT method,url,status,req_headers,res_headers,req_body,res_body,res_blob,size FROM exchange WHERE id=?",
            (int(exchange_id),),
        ).fetchone()
    if not r:
        return f"exchange #{exchange_id} not found"
    m, u, st, rh, sh, rb, sb, blob, sz = r
    out = [f"#{exchange_id} [{st}] {m} {u}", "req headers:", str(rh)[:800], "res headers:", str(sh)[:800]
           , "req body:", str(rb)[:2000] or "(empty)"]
    if blob:
        out.append(f"res body: (blob {blob} — traffic_blob('{blob}') pages it; {sz:,}B)")
    else:
        out.append("res body: " + (str(sb)[:4000] or "(empty)"))
    return "\n".join(out)


def traffic_blob(blob_hash: str, offset: int = 0, chars: int = 5000) -> str:
    """Page a spilled body: offset+chars slice of the blob."""
    p = traffic_db().parent / "blobs" / str(blob_hash)
    if not p.is_file():
        return f"blob {blob_hash} not found"
    t = p.read_text(errors="replace")
    return t[int(offset) : int(offset) + int(chars)] + (f"\n[… {len(t):,} chars — offset to page]" if len(t) > int(offset) + int(chars) else "")
