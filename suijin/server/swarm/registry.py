"""The swarm registry — a swarm is a thing with a definition (operator plan, 2026-11-28).

Today a "swarm" is only a name string shared by rows in swarm.db. This module makes it a
THING: one folder per swarm with ``swarm.json`` as the definition of record — the mission,
the lanes, the agents asked for (host/boxed), the brain, the engine, the status — plus the
journal, the inbox, the exploits and findings, and the agents' workspace in one place::

    ~/.suijin/Targets/<target>/swarms/<swarm-id>/
        swarm.json      the definition (written atomically; the folder is its truth)
        events.jsonl    the journal
        inbox/ exploits/ findings/ home/

Legacy engagements (``~/.suijin/engagements/<slug>``) stay readable — ``list_swarms`` folds
them in as ``legacy`` status so the archive never loses a row; physical migration is its own
tested step (``migrate_legacy``), not a side effect.

Every writer is atomic (tmp + rename) and every reader tolerates a torn file: the registry
is read by the web console, the TUI and the agents at the same time.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

SWARM_STATUSES = ("defining", "running", "paused", "stopped", "done")
_LEGACY_STATUSES = ("legacy",)

#: env override for tests (the tests never write into the real runtime root)
_ROOT_ENV = "SUIJIN_TARGETS_ROOT"


def targets_root() -> Path:
    """~/.suijin/Targets — one folder per target, swarms inside."""
    if os.environ.get(_ROOT_ENV):
        return Path(os.environ[_ROOT_ENV])
    return Path.home() / ".suijin" / "Targets"


def slugify(value: str) -> str:
    """The one slug rule (same as structured._slug, imported lazily when available)."""
    import re

    s = re.sub(r"[^a-z0-9]+", "-", str(value or "target").lower()).strip("-")
    return (s or "target")[:48]


def new_swarm_id(moment: float | None = None) -> str:
    """swarm-2026-11-28-215300 — sortable, unique enough; a suffix breaks ties."""
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.gmtime(moment or time.time()))
    return f"swarm-{stamp}"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def create(
    target: str,
    title: str,
    *,
    lanes: list[str] | None = None,
    host_n: int = 0,
    boxed_n: int = 0,
    brain: dict | None = None,
    engine: str = "auto",
    permissions: str = "",
    mode: str = "",
    chat: str = "",
    swarm_id: str = "",
) -> dict:
    """Define a swarm: make the folder, write swarm.json, return the definition."""
    definition: dict[str, Any] = {
        "id": swarm_id or new_swarm_id(),
        "target": slugify(target),
        "title": str(title or "").strip(),
        "lanes": [str(lane).strip() for lane in (lanes or []) if str(lane).strip()],
        "agents": {"host": max(0, int(host_n or 0)), "boxed": max(0, int(boxed_n or 0))},
        "brain": dict(brain or {}),
        "engine": str(engine or "auto"),
        "permissions": str(permissions or ""),
        "mode": str(mode or ""),
        "chat": str(chat or ""),
        "status": "defining",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    directory = swarm_dir(definition["target"], definition["id"])
    for sub in ("inbox", "exploits", "findings", "home"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / "swarm.json", json.dumps(definition, indent=2, ensure_ascii=False) + "\n")
    return definition


def swarm_dir(target_slug: str, swarm_id: str) -> Path:
    return targets_root() / slugify(target_slug) / "swarms" / swarm_id


def load(swarm_id: str) -> dict | None:
    """The definition by id, searching every target. Torn files read as None."""
    with contextlib.suppress(OSError):
        for path in targets_root().glob("*/swarms/*/swarm.json"):
            if path.parent.name == swarm_id:
                with contextlib.suppress(Exception):
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return data if isinstance(data, dict) else None
    return None


def save(definition: dict) -> dict:
    """Persist a definition (status changes, agent registration). Returns it."""
    swarm_id = str(definition.get("id") or "")
    target = slugify(str(definition.get("target") or ""))
    if not swarm_id or not target:
        raise ValueError("definition needs id and target")
    definition["target"] = target
    _atomic_write(
        swarm_dir(target, swarm_id) / "swarm.json", json.dumps(definition, indent=2, ensure_ascii=False) + "\n"
    )
    return definition


def set_status(swarm_id: str, status: str) -> dict | None:
    if status not in SWARM_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    data = load(swarm_id)
    if data is None:
        return None
    data["status"] = status
    data.setdefault("history", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": status})
    return save(data)


def delete(swarm_id: str) -> bool:
    """The folder and everything in it. Stop first — this is the second half only."""
    data = load(swarm_id)
    if data is None:
        return False
    import shutil

    shutil.rmtree(swarm_dir(str(data["target"]), swarm_id), ignore_errors=True)
    return True


def list_swarms(include_legacy: bool = True) -> list[dict]:
    """Every definition, newest first. Legacy engagement folders fold in (status
    ``legacy``) so the archive and the new layout read as one list."""
    out: list[dict] = []
    with contextlib.suppress(OSError):
        for path in targets_root().glob("*/swarms/*/swarm.json"):
            with contextlib.suppress(Exception):
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id"):
                    data["folder"] = str(path.parent)
                    out.append(data)
    if include_legacy:
        engagements = Path.home() / ".suijin" / "engagements"
        with contextlib.suppress(OSError):
            for meta in engagements.glob("*/engagement.json"):
                with contextlib.suppress(Exception):
                    data = json.loads(meta.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        continue
                    out.append(
                        {
                            "id": meta.parent.name,
                            "target": slugify(str(data.get("target") or data.get("target_label") or meta.parent.name)),
                            "title": str(data.get("title") or data.get("objective") or meta.parent.name),
                            "status": "legacy",
                            "created": str(data.get("created") or data.get("ts") or ""),
                            "folder": str(meta.parent),
                        }
                    )
    out.sort(key=lambda d: str(d.get("created") or ""), reverse=True)
    return out


def migrate_legacy(swarm_id: str, *, dry_run: bool = False) -> dict | None:
    """Move one legacy engagement folder into the Targets layout.

    ``~/.suijin/engagements/<id>`` → ``Targets/<target>/swarms/<id>``. The engagement.json
    metadata folds into swarm.json (status ``stopped`` — the run is not live anymore).
    Returns a report instead of raising; callers decide what to tell the operator.
    """
    engagements = Path.home() / ".suijin" / "engagements"
    source = engagements / swarm_id
    meta_path = source / "engagement.json"
    if not meta_path.exists():
        return {"moved": False, "reason": "no engagement.json — not a real engagement"}
    with contextlib.suppress(Exception):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta = meta if isinstance(meta, dict) else {}
    target = slugify(str(meta.get("target") or meta.get("target_label") or swarm_id))
    destination = swarm_dir(target, swarm_id)
    report: dict[str, Any] = {"moved": False, "target": target, "to": str(destination)}
    if destination.exists():
        report["reason"] = "destination already exists"
        return report
    if dry_run:
        report["dry_run"] = True
        report["moved"] = True
        return report
    destination.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.move(str(source), str(destination))
    definition = {
        "id": swarm_id,
        "target": target,
        "title": str(meta.get("title") or meta.get("objective") or swarm_id),
        "lanes": [],
        "agents": {"host": 0, "boxed": 0},
        "brain": dict(meta.get("brain") or {}),
        "engine": str(meta.get("engine") or "auto"),
        "permissions": str(meta.get("permissions") or ""),
        "mode": str(meta.get("mode") or ""),
        "chat": str(meta.get("chat") or ""),
        "status": "stopped",
        "created": str(meta.get("created") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        "migrated_from": str(source),
    }
    _atomic_write(destination / "swarm.json", json.dumps(definition, indent=2, ensure_ascii=False) + "\n")
    report["moved"] = True
    return report
