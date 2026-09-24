"""Workspace filesystem primitives.

Safe file operations scoped to suijin_agent/ workspace. Every path goes
through _resolve_safe() which rejects .. traversal and absolute paths
outside the workspace.

Ported and simplified from redamon/agentic/workspace_fs.py.
"""

from __future__ import annotations

from pathlib import Path

from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

WORKSPACE_ROOT = WORKSPACE_DIR


def _resolve_safe(file_path: str) -> Path:
    """Resolve a user-supplied path, rejecting traversal escapes."""
    p = Path(file_path)
    if p.is_absolute():
        # Allow only if inside workspace
        resolved = p.resolve()
        if not str(resolved).startswith(str(WORKSPACE_ROOT.resolve())):
            raise ValueError(f"Path outside workspace: {file_path}")
        return resolved

    # Relative — resolve within workspace
    resolved = (WORKSPACE_ROOT / p).resolve()
    if not str(resolved).startswith(str(WORKSPACE_ROOT.resolve())):
        raise ValueError(f"Path traversal detected: {file_path}")
    return resolved


def fs_read(file_path: str) -> str:
    """Read a file from the workspace. Returns content as string."""
    p = _resolve_safe(file_path)
    if not p.exists():
        return f"Error: File not found: {file_path}"
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading {file_path}: {e}"


def fs_write(file_path: str, content: str) -> str:
    """Write content to a file in the workspace. Creates parent dirs."""
    p = _resolve_safe(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {file_path}"
    except Exception as e:
        return f"Error writing {file_path}: {e}"


def fs_append(file_path: str, content: str) -> str:
    """Append content to a file."""
    p = _resolve_safe(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} chars to {file_path}"
    except Exception as e:
        return f"Error appending to {file_path}: {e}"


def fs_list(dir_path: str = "") -> str:
    """List files in a workspace directory."""
    p = _resolve_safe(dir_path) if dir_path else WORKSPACE_ROOT
    if not p.exists():
        return f"Error: Directory not found: {dir_path}"
    try:
        items = []
        for entry in sorted(p.iterdir()):
            suffix = "/" if entry.is_dir() else ""
            size = entry.stat().st_size if entry.is_file() else 0
            items.append(f"  {entry.name}{suffix} ({size} bytes)")
        return "\n".join(items) if items else "(empty)"
    except Exception as e:
        return f"Error listing {dir_path}: {e}"


def fs_delete(file_path: str) -> str:
    """Delete a file or empty directory."""
    p = _resolve_safe(file_path)
    if not p.exists():
        return f"File not found: {file_path}"
    try:
        if p.is_dir():
            p.rmdir()
        else:
            p.unlink()
        return f"Deleted: {file_path}"
    except Exception as e:
        return f"Error deleting {file_path}: {e}"


def fs_mkdir(dir_path: str) -> str:
    """Create a directory in the workspace."""
    p = _resolve_safe(dir_path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"Created directory: {dir_path}"
    except Exception as e:
        return f"Error creating directory {dir_path}: {e}"


def fs_exists(file_path: str) -> str:
    """Check if a file exists."""
    p = _resolve_safe(file_path)
    return f"{'EXISTS' if p.exists() else 'NOT_FOUND'}: {file_path}"


# Workspace paths
def workspace_path() -> str:
    return str(WORKSPACE_ROOT)


def outputs_path() -> str:
    p = WORKSPACE_ROOT / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def payloads_path() -> str:
    p = WORKSPACE_ROOT / "outputs" / "payloads"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def scripts_path() -> str:
    p = WORKSPACE_ROOT / "scripts"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)
