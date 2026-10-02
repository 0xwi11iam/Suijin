"""Chainstate — prerequisite gating that makes the extended chains REAL.

A planted vulnerability that fires standalone is a shooting gallery; a
chained one is an engagement. This module reads the telemetry stream
(the same events the bench scores) and answers one question for the
handlers that care: has this engagement actually walked edge X?

Services are separate processes; the telemetry file is append-only JSONL
under ROOT — mtime-cached so a gated handler costs one stat() when idle.
"""

from __future__ import annotations

import json
import os
import time

from suijin.lab.northbridge import ROOT, telemetry_dir

_cache: dict = {"mtime": 0.0, "edges": set(), "counts": {}}


def _events_path() -> str:
    return os.path.join(telemetry_dir(), "events.jsonl")


def refresh(force: bool = False) -> None:
    p = _events_path()
    try:
        mtime = os.stat(p).st_mtime
    except OSError:
        _cache["mtime"], _cache["edges"], _cache["counts"] = 0.0, set(), {}
        return
    if not force and mtime == _cache["mtime"]:
        return
    edges, counts = set(), {}
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            e = str(rec.get("edge", ""))
            if e:
                edges.add(e)
                counts[e] = counts.get(e, 0) + 1
    _cache.update(mtime=mtime, edges=edges, counts=counts)


def has(edge: str) -> bool:
    """True when the engagement recorded this chain edge."""
    refresh()
    return edge in _cache["edges"]


def count(edge: str) -> int:
    refresh()
    return _cache["counts"].get(edge, 0)


def at_least(edge: str, n: int) -> bool:
    refresh()
    return _cache["counts"].get(edge, 0) >= n


def reset_cache() -> None:
    _cache.update(mtime=0.0, edges=set(), counts={})


def gate_missing(prereq: str, what: str) -> dict:
    """The uniform 'not yet' response for a gated endpoint: honest 200/403
    shape with an in-world hint — the agent earns the next step by doing
    reconnaissance, not by spamming the endpoint."""
    return {
        "ok": False,
        "detail": what,
        "hint": f"requires: {prereq}",
        "_ts": round(time.time(), 3),
    }
