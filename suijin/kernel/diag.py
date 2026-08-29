"""Diagnostic log — the hang/post-mortem trail.

One JSONL line per observable event, written to outputs/logs/diag.log:

  {"t": iso, "kind": "llm", "provider": "zai", "model": "glm-5.3",
   "msgs": 12, "wall_s": 4.2, "ttft_s": 0.8, "ok": true}
  {"t": iso, "kind": "tool", "name": "recon_chain", "wall_s": 10.1,
   "result_chars": 234, "bg": true}
  {"t": iso, "kind": "node", "name": "think", "iteration": 4}

Never blocks, never raises, never grows unbounded (rotates at 10MB).
Every LLM stall, tool hang, or node-level pause is diagnosable post-mortem.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

_MAX_BYTES = 10_000_000  # rotate beyond this


def _log_path() -> Path:
    import os

    # kernel purity: no suijin.modules imports — resolve the workspace dir
    # from the environment or the canonical ~/.suijin/workspace location
    ws = os.environ.get("SUIJIN_WORKSPACE")
    if not ws:
        ws = str(Path.home() / ".suijin" / "workspace")
    d = Path(ws) / "outputs" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "diag.log"


def diag(kind: str, **fields) -> None:
    """Append one diagnostic line. Best-effort; never raises."""
    try:
        p = _log_path()
        if p.exists() and p.stat().st_size > _MAX_BYTES:
            p.rename(p.with_suffix(".1.log"))  # rotate (keeps one prior)
        entry = {"t": datetime.now(timezone.utc).isoformat()[:19] + "Z", "kind": kind, **fields}
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str, separators=(",", ":")) + "\n")
    except Exception:  # noqa: BLE001 — diagnostics must never break the run
        pass


class diag_timer:
    """Context manager: wall-clock a named operation, log on exit."""

    def __init__(self, kind: str, **fields):
        self._kind = kind
        self._fields = fields
        self._t0 = None

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        wall = round(time.monotonic() - self._t0, 2) if self._t0 else 0
        ok = exc[0] is None
        self._fields["wall_s"] = wall
        self._fields["ok"] = ok
        diag(self._kind, **self._fields)
        return False  # never suppress
