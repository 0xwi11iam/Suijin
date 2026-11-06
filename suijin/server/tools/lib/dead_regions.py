"""mark_dead — broadcast a tested-and-dead region to the whole swarm.

The operator directive (2026-11-06): agents broadcast dead regions to
the group chat so the swarm never retraces a corpse. Pairs with the
prior-work gate (that one refuses RE-DERIVING buried findings; this one
stops LIVE peers from retesting a surface another agent already
declared dead).

Flow: mark_dead(region, reason)
  → engagement dead_regions.jsonl (durable, host-mounted — boxes write
    through to the host)
  → /engagement/<eng>/dead on the bus (live peers hear it)
  → every worker's system prompt carries the recent dead list
    (worker_loop._dead_regions_hint) — peers skip without asking.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path


def _dead_file(engagement: str = "_default") -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    eng = str(engagement or "default")
    if eng in ("default", ""):
        eng = "_default"
    d = Path(WORKSPACE_DIR).parent / "engagements" / eng
    d.mkdir(parents=True, exist_ok=True)
    return d / "dead_regions.jsonl"


def _retest_days() -> int:
    """How old a dead verdict may be before it's worth retesting — sites
    change (operator directive). Config ledger.retest_days, default 90."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        cfg = load_config() or {}
        return max(1, int(cfg.get("ledger_retest_days", cfg.get("ledger", {}).get("retest_days", 90))))
    return 90


def _target_of(region: str) -> str:
    from urllib.parse import urlparse

    u = str(region or "")
    host = (urlparse(u).hostname if "://" in u else urlparse("//" + u).hostname or "").lower()
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or "?")


def ledger_rows(target: str = "", engagement: str = "_default") -> list[dict]:
    """The tested ledger: dead regions with AGE and staleness verdict.
    fresh (< retest_days) → peers skip; stale (≥) → 'consider retesting'
    — a year-old dead verdict no longer poisons the swarm."""
    now = time.time()
    days = _retest_days()
    out = []
    for r in recent_dead_regions(engagement, limit=200):
        age_d = (now - float(r.get("ts") or 0)) / 86400
        row = {
            "region": r.get("region", ""),
            "reason": r.get("reason", ""),
            "target": r.get("target") or _target_of(r.get("region", "")),
            "age_days": round(age_d, 1),
            "verdict": "stale — consider retesting" if age_d >= days else "fresh — skip",
        }
        if target and row["target"] != target:
            continue
        out.append(row)
    return list(reversed(out))  # newest first


def record_dead_region(region: str, reason: str = "", engagement: str = "_default") -> str:
    region = str(region or "").strip()
    if not region:
        return "Error: region required (url, endpoint, or surface name)"
    reason = str(reason or "")[:300]
    entry = {"region": region[:300], "reason": reason, "ts": time.time(),
             "target": _target_of(region), "engagement": engagement,
             "agent": __import__("os").environ.get("SUIJIN_IDENTITY", "")}
    with contextlib.suppress(Exception):
        # dedupe: same region declared dead again just refreshes the stamp
        existing = []
        p = _dead_file(engagement)
        if p.exists():
            for ln in p.read_text(errors="replace").splitlines():
                with contextlib.suppress(Exception):
                    e = json.loads(ln)
                    if e.get("region") != region:
                        existing.append(e)
        existing.append(entry)
        p.write_text("\n".join(json.dumps(x) for x in existing[-100:]) + "\n")
    # broadcast to live peers on the bus
    with contextlib.suppress(Exception):
        from suijin.server.mesh.bus import BusClient
        from suijin.server.swarm.lifecycle import _bus_addr

        addr = _bus_addr()
        if addr:
            bc = BusClient()
            if bc.connect(addr[0], addr[1], f"dead-{entry['agent'][:12] or 'reporter'}"):
                bc.publish(f"/engagement/{engagement}/dead", entry)
                bc.close()
    return (
        f"DEAD REGION RECORDED: {region[:80]} — {reason[:80] or 'no reason given'}\n"
        f"peers skip it for {_retest_days()} days; after that the ledger flips "
        "it to 'consider retesting' (sites change). suijin ledger shows the book."
    )


def recent_dead_regions(engagement: str = "_default", limit: int = 10) -> list[dict]:
    with contextlib.suppress(Exception):
        p = _dead_file(engagement)
        if p.exists():
            rows = []
            for ln in p.read_text(errors="replace").splitlines():
                with contextlib.suppress(Exception):
                    rows.append(json.loads(ln))
            return rows[-limit:]
    return []
