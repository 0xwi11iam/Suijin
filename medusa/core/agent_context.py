"""
Per-session agent context — ContextVars for user/project/phase identity.

Lives in its own module so lightweight callers (workspace_fs, job_runner,
output_offload) can import the contextvars without pulling in langchain
or other heavy deps.
"""
from __future__ import annotations
from contextvars import ContextVar
from typing import Optional

current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
current_project_id: ContextVar[str] = ContextVar("current_project_id", default="")
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
current_phase: ContextVar[str] = ContextVar("current_phase", default="informational")


def set_tenant_context(user_id: str, project_id: str, session_id: str = "") -> None:
    """Set the current user, project (and session) context for tools."""
    current_user_id.set(user_id)
    current_project_id.set(project_id)
    current_session_id.set(session_id or "")


def set_phase_context(phase: str) -> None:
    """Set the current phase context for tool restrictions."""
    current_phase.set(phase)


def get_phase_context() -> str:
    """Get the current phase context."""
    return current_phase.get()
