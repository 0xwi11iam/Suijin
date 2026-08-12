"""Workspace path management — extracted from dispatch.py for maintainability."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # medusa/ root
PROJECT_DIR = BASE_DIR.parent  # medusa-security/ root
WORKSPACE_DIR = PROJECT_DIR / "medusa_agent"


def resolve_workspace_path(file_path: str | Path) -> Path:
    """Resolve a file path relative to the agent workspace.

    - Relative paths → resolved from WORKSPACE_DIR
    - Absolute paths → REJECTED unless within WORKSPACE_DIR or allowlisted
    - Symlinks → resolved to real path before boundary check
    """
    p = Path(file_path)
    if p.is_absolute():
        try:
            real = p.resolve()
        except Exception:
            real = p
        # Reject paths outside workspace
        try:
            real.relative_to(WORKSPACE_DIR.resolve())
            return real
        except ValueError:
            allowlisted = ["/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp",
                          os.environ.get("HOME", "/tmp")]
            if any(str(real).startswith(d) for d in allowlisted):
                return real
            raise PermissionError(
                f"Absolute path '{file_path}' resolves to '{real}' which is outside workspace '{WORKSPACE_DIR}'. "
                f"Use a relative path or write to /tmp/."
            )
    return (WORKSPACE_DIR / p).resolve()
