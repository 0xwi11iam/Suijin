"""
Session Replay — save and resume engagements from LangGraph checkpoints.
"""
import json, time
from pathlib import Path
from datetime import datetime, timezone

REPLAY_DIR = Path(__file__).resolve().parent.parent / "medusa_agent" / "sessions"
REPLAY_DIR.mkdir(parents=True, exist_ok=True)


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
        "cost_usd": cost,
    }
    path = REPLAY_DIR / f"{thread_id}_{ts}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return str(path)


def list_sessions() -> list:
    """List all saved sessions."""
    sessions = []
    for f in sorted(REPLAY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            data["_file"] = str(f)
            sessions.append(data)
        except Exception as e:
            import logging; logging.getLogger("medusa").warning(f"Session replay failed: {e}")
            pass
    return sessions


def load_session_summary(session_file: str) -> dict:
    """Load a saved session summary."""
    return json.loads(Path(session_file).read_text())


def get_latest_session() -> dict | None:
    """Get the most recent saved session."""
    sessions = list_sessions()
    return sessions[0] if sessions else None
