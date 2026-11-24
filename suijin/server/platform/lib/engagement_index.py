"""Rebuild ~/.suijin/engagements/index.json from metadata + findings."""
from __future__ import annotations

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
    for folder in sorted(p for p in engagements.iterdir() if p.is_dir()):
        meta_path = folder / "engagement.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            meta = {}
        rows.append({
            "id": folder.name,
            "name": meta.get("name") or folder.name,
            "program": meta.get("program") or meta.get("target") or "",
            "target": meta.get("target") or "",
            "status": meta.get("status") or "active",
            "createdAt": meta.get("createdAt") or "",
            "audit": str(folder / "events.jsonl"),
            "artifacts": str(folder / "exploits"),
            "findings": int(counts.get(folder.name, 0)),
        })
    payload = {"generatedAt": datetime.now(timezone.utc).isoformat(), "engagements": rows}
    (engagements / "index.json").write_text(json.dumps(payload, indent=2))
    return payload
