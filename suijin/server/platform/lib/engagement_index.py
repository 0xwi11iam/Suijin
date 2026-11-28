"""Rebuild ~/.suijin/engagements/index.json from metadata + findings."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def rebuild(root: Path | None = None) -> dict:
    base = Path(root) if root is not None else Path.home() / ".suijin"
    engagements = base / "engagements"
    if not engagements.is_dir():
        return {"engagements": []}
    counts: dict[str, int] = {}
    db_path = base / "swarm" / "swarm.db"
    if db_path.is_file():
        try:
            db = sqlite3.connect(str(db_path), timeout=5)
            counts = dict(db.execute("SELECT engagement, COUNT(*) FROM findings GROUP BY engagement").fetchall())
            db.close()
        except sqlite3.Error:
            counts = {}
    rows = []
    for folder in sorted(p for p in engagements.iterdir() if p.is_dir() and not p.name.startswith(".")):
        meta_path = folder / "engagement.json"
        meta: dict = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, ValueError):
                meta = {}
        events = folder / "events.jsonl"
        if not meta and not events.is_file():
            continue  # nothing ever happened here: not an engagement
        # A folder with a journal and no metadata is still an engagement (the CLI and agent runs
        # never wrote one). Until 2026-11-27 the index skipped exactly those — on one machine the
        # only folder with real evidence was missing from it.
        lines = 0
        if events.is_file():
            with contextlib.suppress(OSError), events.open("rb") as handle:
                lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
        created = meta.get("createdAt") or ""
        if not created and events.is_file():
            with contextlib.suppress(OSError):
                created = datetime.fromtimestamp(events.stat().st_mtime, timezone.utc).isoformat()
        rows.append(
            {
                "id": folder.name,
                "name": meta.get("name") or folder.name,
                "kind": str(meta.get("kind") or "engagement"),
                "program": meta.get("program") or meta.get("target") or "",
                "target": meta.get("target") or "",
                "status": meta.get("status") or "active",
                "createdAt": created,
                "events": lines,
                "audit": str(folder / "events.jsonl"),
                "artifacts": str(folder / "exploits"),
                "findings": int(counts.get(folder.name, 0)),
            }
        )
    payload = {"generatedAt": datetime.now(timezone.utc).isoformat(), "engagements": rows}
    (engagements / "index.json").write_text(json.dumps(payload, indent=2))
    return payload
