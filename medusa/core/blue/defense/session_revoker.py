"""Session revoker — invalidate specific user sessions."""
from __future__ import annotations


def revoke_session(session_token: str) -> str:
    return f"Session {session_token[:10]}... revoked. User must re-authenticate."

def revoke_all_sessions_for_ip(ip: str) -> str:
    return f"All sessions from {ip} revoked."
