"""Derived .sje export — the bundle's graph_state REPLAYED from the log.

The event journal (events.jsonl) is THE truth: resume is replay, and the
.sje bundle is an EXPORT of that log, not a competing record.

Before this module, every exit path serialized whatever in-memory frame
the caller happened to hold — the conclusion path was fine, but the crash
backstops (SIGTERM, excepthook, atexit) captured `agent.get_state()`
MID-TURN, a frame that was never a resume point: a half-written message,
a missing conclusion, an odd number of tool calls. Replaying the log
gives every exit path the SAME resume contract as `suijin resume`: the
full ordered conversation + the last confirmed state.snapshot, with the
caller's live frame overlaid only where the log has no data.

The resume contract holds: completion_reason is deliberately absent (a
loaded bundle must run, not re-complete).
"""

from __future__ import annotations

from pathlib import Path


def derive_graph_state(events_path: str | Path, overlay: dict | None = None) -> dict:
    """Replay the journal into a bundle-ready graph_state.

    Returns the replay subset (messages, trace, snapshots, phase,
    iteration, findings, chain memory, targeting, todos…). The caller's
    live frame only fills GAPS — keys the journal never carries (e.g.
    conversation_objectives) that the caller confirmed — never overrides
    the record. Returns {} when the journal has no usable records (a
    crash before the first session.start: the caller falls back to its
    in-memory state).
    """
    from suijin.modules.agent.lib.event_log import replay

    state = dict(replay(events_path) or {})
    # replay() fabricates `execution_trace: []` even for an empty/absent
    # log — a journal is ONLY usable when it carries a real record
    # (session.start, a snapshot, a message…). Empty → the caller falls
    # back to its in-memory frame.
    if not state or not any(k != "execution_trace" for k in state):
        return {}
    if overlay:
        state.update({k: v for k, v in dict(overlay).items() if k not in state and v is not None})
    return state
