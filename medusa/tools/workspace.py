"""Workspace path management — extracted from dispatch.py for maintainability."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # medusa/ root
PROJECT_DIR = BASE_DIR.parent  # medusa-security/ root
WORKSPACE_DIR = PROJECT_DIR / "medusa_agent"  # the ONE canonical agent workspace


def ensure_workspace_layout(base_dir: Path | None = None, workspace_dir: Path | None = None) -> bool:
    """Enforce the canonical workspace layout.

    The contract (README): agent artifacts live in <repo>/medusa_agent/ and
    medusa/medusa_agent is a symlink -> ../medusa_agent for legacy code that
    still references the inner path. If the inner path exists as a REAL
    directory (the pre-2.6 split-brain layout), its contents are merged into
    the root workspace first — the inner dir holds the live legacy data, so
    it wins on name collisions.

    Idempotent. Returns True if a migration or symlink creation happened.
    """
    base = Path(base_dir) if base_dir else BASE_DIR
    root = Path(workspace_dir) if workspace_dir else WORKSPACE_DIR
    inner = base / "medusa_agent"
    if inner.is_symlink():
        return False
    if inner.exists():
        root.mkdir(parents=True, exist_ok=True)
        _merge_tree(inner, root)
        inner.rmdir()  # empty after the merge
    try:
        inner.symlink_to(os.path.relpath(root, base))
        return True
    except OSError:
        # Symlinks unavailable (unprivileged Windows) — leave an empty dir;
        # all writes go through WORKSPACE_DIR, so nothing lands there anyway.
        inner.mkdir(exist_ok=True)
        return False


def _merge_tree(src: Path, dst: Path) -> None:
    """Move every file/dir under src into dst (recursively, dst wins on dirs)."""
    for item in list(src.iterdir()):
        target = dst / item.name
        if item.is_dir() and not item.is_symlink() and target.is_dir():
            _merge_tree(item, target)
            item.rmdir()
        else:
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))


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
            allowlisted = ["/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", os.environ.get("HOME", "/tmp")]
            if any(str(real).startswith(d) for d in allowlisted):
                return real
            raise PermissionError(
                f"Absolute path '{file_path}' resolves to '{real}' which is outside workspace '{WORKSPACE_DIR}'. "
                f"Use a relative path or write to /tmp/."
            )
    return (WORKSPACE_DIR / p).resolve()
