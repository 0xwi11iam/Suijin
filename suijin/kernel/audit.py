"""Kernel tool audit — the append-only record of every tool invocation.

One JSONL sink per surface under <workspace>/outputs/audit_trails/:
  tool_calls.jsonl  — every Context.call_tool (red, blue, MCP, packs)
  agent_steps.jsonl — agent-loop executions (execute_tool_node)
  cli_calls.jsonl   — CLI verb invocations

Design constraints (kernel purity):
  - stdlib only
  - args are NEVER stored raw: key NAMES plus a length+sha256 digest —
    enough to correlate and detect tampering, useless for secrets
  - append-only: lines are only ever added; flush writes buffered
    entries in order; past lines are immutable
  - failure to record must never break the tool call itself
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

_FLUSH_EVERY = 25


def digest_args(args: dict | None) -> dict:
    """Key names + length + hash of a canonical JSON dump — no raw values."""
    try:
        blob = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(sorted((args or {}).items()))
    return {
        "keys": sorted((args or {}).keys()),
        "n_bytes": len(blob),
        "sha256": hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16],
    }


class ToolAudit:
    """Buffered append-only JSONL audit sink (thread-safe)."""

    def __init__(self, dir_path: str | Path, filename: str, flush_every: int = _FLUSH_EVERY) -> None:
        self._path = Path(dir_path) / filename
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._flush_every = max(1, flush_every)
        self.enabled = True

    def record(
        self,
        *,
        surface: str,
        name: str,
        owner: str = "",
        args: dict | None = None,
        outcome: str = "ok",
        duration_ms: float = 0.0,
        detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "surface": surface,
            "name": name,
            "owner": owner,
            "args": digest_args(args),
            "outcome": outcome,
            "duration_ms": round(duration_ms, 1),
        }
        if detail:
            entry["detail"] = detail[:200]
        line = json.dumps(entry, separators=(",", ":"))
        try:
            with self._lock:
                self._buf.append(line)
                if len(self._buf) >= self._flush_every:
                    self._flush_locked()
        except Exception:  # noqa: BLE001 — auditing must never break the call
            pass

    def flush(self) -> None:
        try:
            with self._lock:
                self._flush_locked()
        except Exception:  # noqa: BLE001
            pass

    def _flush_locked(self) -> None:
        if not self._buf:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(self._buf) + "\n")
        self._buf.clear()

    def entries(self) -> list[dict]:
        """Read the whole sink (flushes first). Test/inspection hook."""
        self.flush()
        if not self._path.exists():
            return []
        out = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out


class NullAudit(ToolAudit):
    """Drop-in for contexts that must not write (unit tests, boot scans)."""

    def __init__(self) -> None:  # deliberately does not call super — inert sink
        self.enabled = False
        self._buf = []
        self._lock = threading.Lock()
        self._flush_every = 1
        self._path = Path("/dev/null")
