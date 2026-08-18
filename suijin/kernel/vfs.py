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
        """Resolve a workspace-relative path (absolute paths pass through)."""
        p = Path(rel)
        if p.is_absolute():
            return p
        return (self.root / p).resolve()

    def is_allowed(self, rel: str | Path) -> bool:
        """True when the RESOLVED path is inside the workspace or an
        explicitly allowed extra. Symlinks resolved first — a link planted
        inside the workspace pointing outside is an escape, not a pass."""
        try:
            real = self.resolve(rel)
        except (OSError, RuntimeError):
            return False
        for base in [self.root, *self._allow]:
            try:
                real.relative_to(base)
                return True
            except ValueError:
                continue
        return False

    def open_for_write(self, rel: str | Path, data: str) -> Path:
        """Boundary-checked write: refuses paths outside the workspace."""
        target = self.resolve(rel)
        if not self.is_allowed(target):
            raise PermissionError(f"vfs: path '{rel}' escapes workspace {self.root}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
        return target
