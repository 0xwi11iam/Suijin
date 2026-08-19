"""Kernel VFS — the single file-chokepoint every module's file access
routes through. Workspace-anchored with optional allowlist extras;
symlinks resolved before boundary checks. Stdlib only.
"""

from __future__ import annotations

from pathlib import Path


class Vfs:
    """Path resolution + boundary enforcement for one workspace root."""

    def __init__(self, root: Path, allow: list[Path] | None = None) -> None:
        self.root = Path(root).resolve()
        self._allow = [Path(p).resolve() for p in (allow or [])]

    def resolve(self, rel: str | Path) -> Path:
        """Resolve a path against the workspace root, symlink-normalized.

        Absolute paths are ALSO resolved — on macOS /tmp is a symlink to
        /private/tmp, so comparing a raw absolute against the resolved
        root would wrongly reject the workspace's own canonical path.
        (Lexical .. segments would be collapsed by resolve(); escapes
        are caught afterwards by is_allowed's prefix check.)"""
        p = Path(rel)
        if p.is_absolute():
            return p.resolve()
        return (self.root / p).resolve()

    def is_allowed(self, rel: str | Path) -> bool:
        """True when the RESOLVED path is inside the workspace or an
        explicitly allowed extra. The root itself (and its trailing-slash
        spelling) is allowed — the workspace root is IN the workspace.
        Symlinks resolved first — a link planted inside the workspace
        pointing outside is an escape, not a pass."""
        try:
            real = self.resolve(rel)
        except (OSError, RuntimeError):
            return False
        real_s = str(real).rstrip("/")
        for base in [self.root, *self._allow]:
            base_s = str(base).rstrip("/")
            if real_s == base_s or real_s.startswith(base_s + "/"):
                return True
        return False

    def open_for_write(self, rel: str | Path, data: str) -> Path:
        """Boundary-checked write: refuses paths outside the workspace."""
        target = self.resolve(rel)
        if not self.is_allowed(target):
            raise PermissionError(f"vfs: path '{rel}' escapes workspace {self.root}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
        return target
