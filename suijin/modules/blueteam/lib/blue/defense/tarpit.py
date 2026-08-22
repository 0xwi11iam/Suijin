"""Tarpit — the /tmp/blue_tarpit.json file protocol, unified.

One writer shape, three readers (blue_target lab app, the forward proxy,
the TUI feed). Protocol: a JSON object keyed by IP —

    {"<ip>": {"delay": <seconds>, "since": <epoch>, ...extra}}

`delay` is capped at 15s per request; entries expire 30 minutes after
`since`. The key is "since" — a battle watchdog once wrote "set_at",
which every reader ignored (tarpits moved the scoreboard but never
delayed red). All writers now go through engage() so that cannot
recur; readers use delay_for() or the same tolerant semantics inline
(the lab app stays dependency-free on purpose).
"""

from __future__ import annotations

import json
import time

WINDOW_S = 1800  # tarpit entries live 30 minutes
MAX_DELAY_S = 15.0


def _tarpit_file(path=None):
    if path is not None:
        return path
    from suijin.modules.platform.lib.constants import BLUE_TARPIT_FILE

    return BLUE_TARPIT_FILE


def engage(ip: str, delay: float, path=None, **extra) -> None:
    """Engage (or refresh) the tarpit for one IP. Tolerant: never raises."""
    try:
        from pathlib import Path

        p = Path(_tarpit_file(path))
        state = {}
        if p.exists():
            try:
                state = json.loads(p.read_text())
            except ValueError:
                state = {}
        state[ip] = {"delay": min(float(delay), MAX_DELAY_S), "since": time.time(), **extra}
        p.write_text(json.dumps(state))
    except Exception:  # noqa: BLE001 — deception must never break the loop
        pass


def delay_for(ip: str, path=None, now: float | None = None) -> float:
    """Seconds the reader should sleep for this IP (0 = no tarpit)."""
    try:
        from pathlib import Path

        p = Path(_tarpit_file(path))
        if not p.exists():
            return 0.0
        state = json.loads(p.read_text())
        entry = state.get(ip)
        if not isinstance(entry, dict):
            return 0.0
        since = entry.get("since", 0)
        if (now if now is not None else time.time()) - since >= WINDOW_S:
            return 0.0  # expired
        return min(float(entry.get("delay", 5.0)), MAX_DELAY_S)
    except Exception:  # noqa: BLE001 — malformed state = no delay
        return 0.0
