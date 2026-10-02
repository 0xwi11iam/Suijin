"""Adaptive defense — the lab pushes back like production does.

Two behaviors, both in-world:
- CIRCUIT BREAKER: a service that absorbs too many error-probes (404/400
  spam, scanner shapes) trips into LOCKDOWN for a cooldown — every
  request 429s with an honest Retry-After. Pacing is a real skill; the
  lab now tests it.
- EDGE TARPIT: the gateway's canary family learns. Repeated canary hits
  put the client into a 404-everything mode for a window — the agent
  must rotate approach or wait, exactly like a WAF that has your number.

Counters live under ROOT (shared by the service processes), keyed by
service + bucket, swept by reset().
"""

from __future__ import annotations

import json
import os
import time

from suijin.lab.northbridge import ROOT

#: probes tolerated per service before the breaker trips
BREAKER_THRESHOLD = 40
BREAKER_COOLDOWN_S = 25.0
#: canary hits before the tarpit closes on a client
TARPIT_THRESHOLD = 3
TARPIT_WINDOW_S = 60.0

_state: dict = {"data": None, "mtime": 0.0}


def _path() -> str:
    return os.path.join(ROOT, "defense.json")


def _load() -> dict:
    try:
        mtime = os.stat(_path()).st_mtime
    except OSError:
        return {"probes": {}, "tripped": {}, "canary": {}, "tarpit": {}}
    if _state["data"] is None or mtime != _state["mtime"]:
        try:
            with open(_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {"probes": {}, "tripped": {}, "canary": {}, "tarpit": {}}
        _state.update(data=data, mtime=mtime)
        return data
    return _state["data"]


def _save(data: dict) -> None:
    os.makedirs(ROOT, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _path())
    _state.update(data=data, mtime=os.stat(_path()).st_mtime)


def record_probe(service: str) -> None:
    """A 40x landed on a service (a probe). Trips the breaker past the
    threshold; the trip itself is data the agent can observe (429s)."""
    d = _load()
    now = time.time()
    key = f"{service}:{int(now // 60)}"  # per-minute buckets
    hits = d["probes"].get(key, 0) + 1
    d["probes"][key] = hits
    d["probes"] = {k: v for k, v in d["probes"].items() if int(k.split(":")[-1]) >= int(now // 60) - 2}
    if hits >= BREAKER_THRESHOLD:
        d["tripped"][service] = now + BREAKER_COOLDOWN_S
    _save(d)


def locked_down(service: str) -> float:
    """Seconds remaining in lockdown, 0 when clear."""
    d = _load()
    until = float(d["tripped"].get(service, 0))
    return max(0.0, until - time.time())


def record_canary(client: str) -> None:
    d = _load()
    now = time.time()
    hits = [t for t in d["canary"].get(client, []) if now - t < TARPIT_WINDOW_S]
    hits.append(now)
    d["canary"][client] = hits
    if len(hits) >= TARPIT_THRESHOLD:
        d["tarpit"][client] = now + TARPIT_WINDOW_S
    _save(d)


def tar_pitted(client: str) -> bool:
    d = _load()
    until = float(d["tarpit"].get(client, 0))
    return until > time.time()
