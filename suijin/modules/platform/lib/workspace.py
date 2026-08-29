"""Workspace path management — extracted from dispatch.py for maintainability."""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]  # suijin/ root
PROJECT_DIR = BASE_DIR.parent  # repo root


def _resolve_workspace() -> Path:
    """The ONE canonical agent workspace — durable across reinstalls.

    Resolution order:
      1. SUIJIN_WORKSPACE env (explicit override)
      2. ~/.suijin/workspace — the durable home (created by install.sh;
         survives repo re-clones, reinstalls and dev-tree wipes)
      3. repo-local suijin_agent (source checkouts, tests, back-compat) —
         if it is a symlink (install.sh wires this), follow it

    The workspace holds sessions, memory, the authorization ledger,
    bugscope pulls and reports — losing it to a reinstall lost every
    engagement artifact at once (field complaint)."""
    env = os.environ.get("SUIJIN_WORKSPACE")
    if env:
        return Path(env).expanduser()
    durable = Path.home() / ".suijin" / "workspace"
    if durable.is_dir():
        return durable
    local = PROJECT_DIR / "suijin_agent"
    if local.is_symlink():
        # installed/dev layout wires suijin_agent -> the durable home.
        # Follow it ONLY when the target exists — a dangling symlink (a
        # fresh clone of a repo that accidentally committed the link)
        # must not leak an un-creatable path into module-level mkdirs
        # (CI died creating '/Users' on Linux). Dangling -> durable.
        try:
            resolved = local.resolve()
            if resolved.is_dir() and not resolved.is_symlink():
                return resolved
        except OSError:
            pass
        return durable
    return local


WORKSPACE_DIR = _resolve_workspace()

# v4.2: ALL agent artifacts nest under outputs/ — one parent for everything
# the engagement produces. State/config (blue_config.json, notify.json,
# approvals.json, engagement_schema.json, caches/) stays at the workspace
# root by design.
ARTIFACT_DIRS = (
    "reports",
    "dossiers",
    "exports",
    "sessions",
    "audit_trails",
    "blue_state",
    "compliance_reports",
    "wordlists",
    "payloads",
    "sandbox",
    "spar_baselines",
    "memory",
    "evidence",
    "engagement_templates",
    "portal",
    "bugscope",
    "fireteam",
    "engagements",  # per-engagement state (schema/recovery/scratchpad/approvals)
    "archive",  # ended engagements land here (timestamped, immutable)
)


def artifact_dir(name: str) -> Path:
    """Canonical home for one artifact category (honours patched WORKSPACE_DIR)."""
    if name not in ARTIFACT_DIRS:
        raise ValueError(f"unknown artifact dir {name!r} (one of {ARTIFACT_DIRS})")
    return WORKSPACE_DIR / "outputs" / name


# ── per-engagement state (the immortal-root-state fix) ─────────────────
_CURRENT_ENGAGEMENT: Path | None = None


def _slugify(text: str) -> str:
    import re

    words = re.sub(r"[^a-zA-Z0-9.-]+", "_", str(text or "engagement")).strip("_")
    return (words[:48] or "engagement")


def set_engagement(objective: str = "") -> Path:
    """Scope all per-engagement state (schema, recovery, scratchpad,
    approvals) to outputs/engagements/<slug>/ — state DIES with its
    engagement instead of accumulating at the workspace root forever."""
    global _CURRENT_ENGAGEMENT
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    d = artifact_dir("engagements") / f"{stamp}_{_slugify(objective[:60])}"
    d.mkdir(parents=True, exist_ok=True)
    _CURRENT_ENGAGEMENT = d
    return d


def engagement_dir() -> Path:
    """The current engagement's state dir (auto-created; `_default` before
    set_engagement boots — legacy callers keep working)."""
    d = _CURRENT_ENGAGEMENT or artifact_dir("engagements") / "_default"
    with contextlib.suppress(OSError):
        d.mkdir(parents=True, exist_ok=True)
    return d


def archive_engagement(reason: str = "ended") -> Path | None:
    """Move the current engagement's state dir into outputs/archive/
    (timestamped) — the .sje bundle remains the resume artifact."""
    global _CURRENT_ENGAGEMENT
    if _CURRENT_ENGAGEMENT is None:
        return None
    from datetime import datetime, timezone
    from shutil import move

    src = _CURRENT_ENGAGEMENT
    _CURRENT_ENGAGEMENT = None
    if not src.is_dir() or not any(src.iterdir()):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = artifact_dir("archive") / f"{stamp}_{src.name}_{_slugify(reason)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        move(str(src), str(dest))
        return dest
    except OSError:
        return None


def migrate_legacy_artifacts(workspace: Path | None = None) -> list[str]:
    """One-time move: root-level artifact dirs -> outputs/<name> (merge).

    Idempotent; returns the moved names. Never raises into the caller."""
    moved = []
    try:
        ws = Path(workspace) if workspace else WORKSPACE_DIR
        out_root = ws / "outputs"
        for name in ARTIFACT_DIRS:
            legacy = ws / name
            if not legacy.is_dir():
                continue
            target = out_root / name
            target.mkdir(parents=True, exist_ok=True)
            for item in list(legacy.iterdir()):
                dest = target / item.name
                if dest.exists():
                    if item.name == ".DS_Store":
                        item.unlink(missing_ok=True)
                    continue  # target wins; keep both eras' files otherwise
                item.rename(dest)
            if not any(legacy.iterdir()):
                legacy.rmdir()
            moved.append(name)
    except OSError:
        pass
    return moved


# Pre-rename (Medusa era) names — data under them is migrated, never dropped.
_LEGACY_ROOT_NAMES = ("medusa_agent",)
_LEGACY_INNER_NAMES = ("medusa_agent",)


def ensure_workspace_layout(base_dir: Path | None = None, workspace_dir: Path | None = None) -> bool:
    """Enforce the canonical workspace layout.

    The contract (README): agent artifacts live in <repo>/suijin_agent/ and
    suijin/suijin_agent is a symlink -> ../suijin_agent for legacy code that
    still references the inner path. If the inner path exists as a REAL
    directory (the pre-2.6 split-brain layout), its contents are merged into
    the root workspace first — the inner dir holds the live legacy data, so
    it wins on name collisions.

    Also migrates the pre-rename workspace name: a legacy medusa_agent/
    root is renamed (or merged, when both exist) into suijin_agent/, and
    stale legacy inner symlinks are removed.

    Idempotent. Returns True if a migration or symlink creation happened.
    """
    base = Path(base_dir) if base_dir else BASE_DIR
    root = Path(workspace_dir) if workspace_dir else WORKSPACE_DIR
    inner = base / "suijin_agent"
    migrated = False

    # legacy root: rename when root is absent, merge when both exist
    for legacy in _LEGACY_ROOT_NAMES:
        legacy_root = root.parent / legacy
        if legacy_root.exists() and not legacy_root.is_symlink():
            if not root.exists():
                shutil.move(str(legacy_root), str(root))
                migrated = True
            elif root.is_dir() and legacy_root.resolve() != root.resolve():
                _merge_tree(legacy_root, root)
                legacy_root.rmdir()
                migrated = True

    # stale legacy inner entries (e.g. suijin/medusa_agent symlink/dir)
    for legacy in _LEGACY_INNER_NAMES:
        legacy_inner = base / legacy
        if legacy_inner.is_symlink():
            legacy_inner.unlink()
        elif legacy_inner.exists():
            root.mkdir(parents=True, exist_ok=True)
            _merge_tree(legacy_inner, root)
            legacy_inner.rmdir()
            migrated = True

    if inner.is_symlink():
        return migrated
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
        return migrated


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

    - Relative paths -> resolved from WORKSPACE_DIR
    - Absolute paths -> REJECTED unless within WORKSPACE_DIR or allowlisted
    - Symlinks -> resolved to real path before boundary check
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
