"""Engagement event log — append-only, records only, THE truth.

Every meaningful record of an engagement (completed decisions, tool calls
and results, phase transitions, guidance, usage, the ending) is appended
to ``engagements/<id>/events.jsonl`` BEFORE the in-process consumers see
it. Durability by construction:

  - resume = replay (``replay()`` reconstructs AgentState from the log)
  - a crash leaves a valid log (atomic lines; a torn tail is dropped)
  - the ``.sje`` bundle becomes a derived export, not the recovery story

Deltas (streaming tokens) are deliberately NOT persisted — they are
rendering, not records.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path

EVENT_VERSION = 1
TOOL_OUTPUT_CAP = 64_000

_KNOWN_KINDS = frozenset(
    {
        "session.start",
        "iteration",
        "phase.transition",
        "assistant.message",
        "tool.call",
        "tool.result",
        "state.snapshot",
        "guidance.delivered",
        "usage",
        "session.complete",
        "session.interrupt",
    }
)


class EventLog:
    """Single-writer append-only log for one engagement. Never raises into
    the loop — every write is best-effort and a closed log silently
    accepts appends (the engagement is over; the record exists in memory
    consumers only)."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._seq = 0
        self._fh = None
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # continue an existing log (resume): seq continues past the tail
            with contextlib.suppress(Exception):
                for line in self._path.read_text(errors="ignore").splitlines():
                    try:
                        rec = json.loads(line)
                        self._seq = max(self._seq, int(rec.get("seq") or 0))
                    except ValueError:
                        continue
            self._fh = self._path.open("a", encoding="utf-8")

    # ── write ────────────────────────────────────────────────────────
    def append(self, kind: str, **fields) -> int:
        """One record. Returns the seq (0 when the log is closed/failed)."""
        if kind not in _KNOWN_KINDS:
            return 0
        with self._lock:
            if self._fh is None:
                return 0
            self._seq += 1
            rec = {
                "v": EVENT_VERSION,
                "seq": self._seq,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "kind": kind,
            }
            rec.update({k: v for k, v in fields.items() if v is not None})
            try:
                self._fh.write(json.dumps(rec, default=str) + "\n")
                self._fh.flush()
                return self._seq
            except OSError:
                return 0

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                with contextlib.suppress(OSError):
                    self._fh.close()
                self._fh = None

    @property
    def path(self) -> Path:
        return self._path


def tool_output_cap(output: str) -> tuple[str, bool]:
    """(capped output, truncated?) — the log keeps ≤64k per tool result."""
    text = str(output or "")
    if len(text) <= TOOL_OUTPUT_CAP:
        return text, False
    return text[:TOOL_OUTPUT_CAP], True


# ── replay ─────────────────────────────────────────────────────────────


def read_events(path: Path) -> list[dict]:
    """Parse a log, dropping any torn tail line (a crash mid-write)."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict] = []
    for line in p.read_text(errors="ignore").splitlines():
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and rec.get("kind"):
                out.append(rec)
        except ValueError:
            continue  # torn tail
    return out


#: the replay subset of AgentState — the same contract as the .sje
REPLAY_KEYS = (
    "messages",
    "original_objective",
    "current_objective_index",
    "objective_history",
    "current_phase",
    "phase_history",
    "current_iteration",
    "execution_trace",
    "todo_list",
    "target_info",
    "chain_findings_memory",
    "chain_failures_memory",
    "tested_axes",
    "findings",
    "_attack_queue",
    "_foothold_at",
)

#: resume-critical state the RUNNER snapshots each iteration into a
#: state.snapshot record. Multi-value fields that a plain event stream
#: cannot reconstruct (todo list, findings, chain memory, targeting,
#: productivity bookkeeping) — WITHOUT them a replay would resume into a
#: blank slate. Messages / execution_trace / iteration / phase stay
#: event-derived (ordered, richer); the last snapshot overlays the rest.
RESUME_SNAPSHOT_KEYS = (
    "original_objective",
    "conversation_objectives",
    "current_objective_index",
    "objective_history",
    "current_phase",
    "phase_history",
    "current_iteration",
    "todo_list",
    "target_info",
    "chain_findings_memory",
    "chain_failures_memory",
    "tested_axes",
    "findings",
    "_attack_queue",
    "_foothold_at",
    "attack_path_type",
    "pending_questions",
)


def replay(path: Path) -> dict:
    """Reconstruct a resume-able AgentState from the event log. Mirrors the
    .sje contract (completion_reason deliberately absent: a replayed run
    must run, not re-complete)."""
    state: dict = {}
    tool_by_id: dict[str, dict] = {}
    trace: list[dict] = []
    snapshot: dict | None = None
    for ev in read_events(path):
        k = ev.get("kind")
        if k == "session.start":
            state.setdefault("original_objective", ev.get("objective", ""))
        elif k == "iteration":
            state["current_iteration"] = int(ev.get("n") or 0)
            if ev.get("phase"):
                state["current_phase"] = ev["phase"]
        elif k == "phase.transition":
            state["current_phase"] = ev.get("to") or state.get("current_phase")
            hist = list(state.get("phase_history") or [])
            hist.append({"phase": state["current_phase"], "ts": ev.get("ts", "")})
            state["phase_history"] = hist
        elif k == "assistant.message":
            msgs = list(state.get("messages") or [])
            msgs.append({"role": "assistant", "content": str(ev.get("content") or "")})
            state["messages"] = msgs
        elif k == "state.snapshot":
            # last one wins — the most recent post-turn state is the resume point
            snapshot = {key: ev[key] for key in RESUME_SNAPSHOT_KEYS if key in ev}
        elif k == "tool.call":
            tool_by_id[str(ev.get("id"))] = {
                "tool_name": ev.get("name"),
                "tool_args": ev.get("args") or {},
                "iteration": int(ev.get("seq") or 0),
            }
        elif k == "tool.result":
            tid = str(ev.get("id"))
            call = tool_by_id.pop(tid, {})
            trace.append(
                {
                    "tool_name": call.get("tool_name") or "?",
                    "tool_args": call.get("tool_args") or {},
                    "tool_output": str(ev.get("output") or ""),
                    "success": bool(ev.get("ok")),
                    "iteration": call.get("iteration") or 0,
                    "error_kind": ev.get("error_kind"),
                }
            )
        elif k == "guidance.delivered":
            msgs = list(state.get("messages") or [])
            msgs.append({"role": "user", "content": str(ev.get("text") or "")})
            state["messages"] = msgs
    if snapshot:
        # The snapshot is the authoritative resume point for multi-value
        # state; messages/trace stay event-derived (ordered, richer).
        state.update(snapshot)
    state["execution_trace"] = trace[-150:]
    if state.get("messages") and len(state["messages"]) > 80:
        keep = list(state["messages"][-80:])
        keep[0] = dict(keep[0])
        keep[0]["content"] = (
            "(context replayed from the event log — older turns trimmed)\n" + str(keep[0].get("content", ""))[:200]
        )
        state["messages"] = keep
    return {k: v for k, v in state.items() if k in REPLAY_KEYS}
