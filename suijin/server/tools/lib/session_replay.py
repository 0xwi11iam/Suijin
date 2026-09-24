"""
Session Replay — save and resume engagements from LangGraph checkpoints.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _replay_dir():
    """sessions dir (honours a monkeypatched module attr)."""
    v = globals().get("REPLAY_DIR")
    if v is not None:
        return v

    from suijin.modules.platform.lib.workspace import artifact_dir as _ad

    return _ad("sessions")


def __getattr__(name):
    if name == "REPLAY_DIR":
        return _replay_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_replay_dir().mkdir(parents=True, exist_ok=True)


def save_session(thread_id: str, objective: str, config: dict, state: dict, cost: float = 0):
    """Save agent session state for later replay."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    data = {
        "thread_id": thread_id,
        "objective": objective,
        "saved_at": ts,
        "config": config,
        "state_summary": {
            "phase": state.get("current_phase", "?"),
            "iterations": state.get("current_iteration", 0),
            "trace_count": len(state.get("execution_trace", [])),
            "message_count": len(state.get("messages", [])),
            "completion_reason": state.get("completion_reason", ""),
        },
        "prompt_profile": state.get("_prompt_profile") or {},
        "prompt_profile_trend": state.get("_prompt_profile_trend") or [],
        "cost_usd": cost,
    }
    path = _replay_dir() / f"{thread_id}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return str(path)


def list_sessions() -> list:
    """List all saved sessions."""
    sessions = []
    for f in sorted(_replay_dir().glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            data["_file"] = str(f)
            sessions.append(data)
        except Exception as e:
            import logging

            logging.getLogger("suijin").warning(f"Session replay failed: {e}")
            pass
    return sessions


def load_session_summary(session_file: str) -> dict:
    """Load a saved session summary."""
    return json.loads(Path(session_file).read_text())


def get_latest_session() -> dict | None:
    """Get the most recent saved session."""
    sessions = list_sessions()
    return sessions[0] if sessions else None


# ── C25: session time-travel ───────────────────────────────────────────


def fork_from_iteration(
    source_session: Path, iteration: int, fork_objective: str = "", dest_dir: Path | None = None
) -> Path:
    """Copy a saved session truncated at iteration N — a fork point for a
    different strategy. Messages/trace beyond N are dropped; the fork
    keeps everything up to and including N."""
    import json as _json

    data = _json.loads(Path(source_session).read_text())
    state = dict(data.get("state_summary") or {})
    trace = list(data.get("iterations") or [])
    kept = [t for t in trace if int(t.get("iteration", 0)) <= iteration]
    if not kept:
        raise ValueError(f"no iterations <= {iteration} in this session")
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fork = {
        "thread_id": f"fork_{Path(source_session).stem}_{iteration}_{ts}",
        "objective": fork_objective or f"{data.get('objective', '?')} (forked @it{iteration})",
        "forked_from": str(source_session),
        "forked_at_iteration": iteration,
        "saved_at": ts,
        "state_summary": {**state, "iterations": iteration, "trace_count": len(kept), "completion_reason": ""},
        "iterations": kept,
        "cost_usd": 0.0,
        "fork_note": f"resume with a different strategy from iteration {iteration}",
    }
    out_dir = Path(dest_dir) if dest_dir else _replay_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{fork['thread_id']}.json"
    out.write_text(_json.dumps(fork, indent=2))
    return out
