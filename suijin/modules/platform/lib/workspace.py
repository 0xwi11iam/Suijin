"""Workspace path management — the ONE owner of every path (2026-09-16 restructure).

The layout (per-engagement containment — nothing escapes the engagement folder):

    workspace/
    ├── profiles/<name>/{SOUL.md, rules.md, config.json}   # CTF + BBP seeded
    ├── engagements/<stamp>_<slug>/                         # generated at start
    │   ├── home/          # the agent's ~ — downloads, loot, scratch (its cwd)
    │   ├── .notes/        # engagement notes (write_note)
    │   ├── exploits/EXP-NNN/{description.md, exploit.yaml, run-N.log}
    │   ├── audit_trails/
    │   ├── dossiers/
    │   ├── toollogs/      # nmap_scan_*.txt, execute_terminal_*.txt
    │   ├── reports/
    │   └── state/         # scratchpad, guidance, questions, coverage, .sje
    ├── skills/            # SKILL.md library (knowledge, like profiles)
    ├── exports/           # the .sje inbox (resume artifacts)
    ├── logs/              # crash/engage logs
    └── archive/           # ended engagements (immutable)

Everything the engagement produces lives inside its folder and dies with it
(archived on end). The agent's files land in home/ — no junk outside, ever.
"""

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
    """
    env = os.environ.get("SUIJIN_WORKSPACE")
    if env:
        return Path(env).expanduser()
    durable = Path.home() / ".suijin" / "workspace"
    if durable.is_dir():
        return durable
    local = PROJECT_DIR / "suijin_agent"
    if local.is_symlink():
        try:
            resolved = local.resolve()
            if resolved.is_dir() and not resolved.is_symlink():
                return resolved
        except OSError:
            pass
        return durable
    return local


WORKSPACE_DIR = _resolve_workspace()

#: everything the engagement owns — created by set_engagement(). The
#: operator ruling (2026-09-17): ONLY knowledge libraries and artifacts
#: that must outlive the engagement stay global; everything else lives
#: and dies with its session folder.
ENGAGEMENT_SUBDIRS = (
    "home",  # the agent's USERLAND (see home_dir) — the write-jail root
    ".notes",  # engagement notes
    "exploits",  # EXP-NNN/{description.md, exploit.yaml, run-N.log}
    "audit_trails",
    "dossiers",
    "toollogs",  # timestamped tool dumps (nmap, terminal, ...)
    "reports",
    "log",  # engagement.log — the logging subprocess's per-run file
    "state",  # scratchpad, live guidance, questions, coverage, .sje
    "memory",  # per-engagement intel (fresh session = fresh memory)
    "sessions",  # session snapshots for THIS engagement
)

#: the userland seeded inside every engagement home/ — a small
#: Linux-feeling user space: the agent feels at home, operators read
#: artifacts where they expect them (v3, 2026-09-23)
HOME_USERLAND = (
    "Downloads",
    "Documents",
    "Pictures",
    ".config",
    ".local/share",
    ".ssh",  # keys the agent generates live WITH the engagement
)

#: routed INSIDE the engagement on demand (modules that predate the
#: restructure keep their category names — the paths follow the layout)
ENGAGEMENT_LAZY = (
    "blue_state",
    "bugscope",
    "compliance_reports",
    "engagement_templates",
    "evidence",
    "fireteam",
    "portal",
    "spar_baselines",
    "wordlists",
    "payloads",
    "sandbox",
)

#: workspace-global: knowledge libraries + survival artifacts
GLOBAL_DIRS = (
    "profiles",  # <name>/{SOUL.md, rules.md, config.json} — operator-owned
    "skills",  # the SKILL.md library (imported knowledge, like profiles)
    "logs",  # crash/engage logs that must survive engagement end
)

#: RETIRED (v3): exports/ and archive/ — the .sje bundle and everything
#: else live INSIDE the engagement folder, which stays in place at run
#: end. Legacy callers that still ask for these resolve to logs/ or the
#: engagement root so nothing breaks on old workspaces.

#: names accepted by artifact_dir() — engagement-scoped when an engagement
#: is active, global otherwise (legacy callers keep working)
ARTIFACT_DIRS = tuple(ENGAGEMENT_SUBDIRS) + tuple(ENGAGEMENT_LAZY) + GLOBAL_DIRS


def _ensure(path: Path) -> Path:
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_global_layout() -> None:
    """Create the workspace-global skeleton (idempotent, never raises)."""
    for name in GLOBAL_DIRS:
        _ensure(WORKSPACE_DIR / name)


def artifact_dir(name: str) -> Path:
    """Canonical home for one artifact category.

    Engagement-scoped categories resolve INSIDE the current engagement when
    one is active (auto-created `_default` otherwise); global categories
    always resolve at the workspace root.
    """
    if name in GLOBAL_DIRS:
        return _ensure(WORKSPACE_DIR / name)
    if name in ("exports", "archive"):
        # v3 retirees: the .sje lives in the engagement now; old callers
        # (bundle pickers on legacy workspaces) get the engagement root
        return _ensure(engagement_dir())
    if name not in ENGAGEMENT_SUBDIRS and name not in ENGAGEMENT_LAZY:
        raise ValueError(f"unknown artifact dir {name!r} (one of {ARTIFACT_DIRS})")
    return _ensure(engagement_dir() / name)


# ── per-engagement state ──────────────────────────────────────────────
_CURRENT_ENGAGEMENT: Path | None = None


def _reset_engagement() -> None:
    """Test seam: clear the active engagement (hermetic fixtures)."""
    global _CURRENT_ENGAGEMENT
    _CURRENT_ENGAGEMENT = None


def _slugify(text: str) -> str:
    import re

    words = re.sub(r"[^a-zA-Z0-9.-]+", "_", str(text or "engagement")).strip("_")
    return words[:48] or "engagement"


def set_engagement(objective: str = "") -> Path:
    """Create the engagement folder — everything it produces lives inside —
    and scope all per-engagement state to it."""
    global _CURRENT_ENGAGEMENT
    from datetime import datetime, timezone

    ensure_global_layout()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    d = WORKSPACE_DIR / "engagements" / f"{stamp}_{_slugify(objective[:60])}"
    for sub in ENGAGEMENT_SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    _CURRENT_ENGAGEMENT = d
    return d


def engagement_dir() -> Path:
    """The current engagement's folder (auto-created; `_default` before
    set_engagement boots — legacy callers keep working)."""
    d = _CURRENT_ENGAGEMENT or WORKSPACE_DIR / "engagements" / "_default"
    return _ensure(d)


def home_dir() -> Path:
    """The agent's USERLAND for this engagement — a small Linux-feeling
    home (Downloads/ Documents/ .config/ ...) so tools that assume a
    user space behave naturally. This is the WRITE-JAIL ROOT: everything
    the agent writes resolves inside the engagement folder."""
    h = _ensure(engagement_dir() / "home")
    for sub in HOME_USERLAND:
        with contextlib.suppress(OSError):
            (h / sub).mkdir(parents=True, exist_ok=True)
    return h


def notes_dir() -> Path:
    """Engagement notes (write_note) — inside the engagement, always."""
    return _ensure(engagement_dir() / ".notes")


def exploits_dir() -> Path:
    """The engagement's exploit catalog root (EXP-NNN/ folders)."""
    return _ensure(engagement_dir() / "exploits")


def state_dir() -> Path:
    """Per-engagement state files (scratchpad, guidance, questions, .sje)."""
    return _ensure(engagement_dir() / "state")


def toollogs_dir() -> Path:
    """Timestamped tool output dumps (nmap_scan_*, execute_terminal_*)."""
    return _ensure(engagement_dir() / "toollogs")


def profiles_root() -> Path:
    """File-backed profile folders: <name>/{SOUL.md, rules.md, config.json}."""
    return _ensure(WORKSPACE_DIR / "profiles")


def archive_engagement(reason: str = "ended") -> Path | None:
    """Move the engagement folder into archive/ (timestamped, immutable) —
    the .sje bundle inside state/ remains the resume artifact."""
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
    dest = WORKSPACE_DIR / "archive" / f"{stamp}_{src.name}_{_slugify(reason)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        move(str(src), str(dest))
        return dest
    except OSError:
        return None


def resolve_workspace_path(file_path: str | Path) -> Path:
    """Resolve a file path relative to the agent's HOME (engagement home
    when active, workspace root otherwise — the agent's ~).

    - Relative paths -> resolved from the agent home
    - Absolute paths -> REJECTED unless within WORKSPACE_DIR or allowlisted
    - Symlinks -> resolved to real path before boundary check
    """
    p = Path(file_path)
    if p.is_absolute():
        try:
            real = p.resolve()
        except Exception:
            real = p
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
    return (home_dir() / p).resolve()


# ── legacy-layout compatibility (one release) ─────────────────────────


def migrate_legacy_artifacts(workspace: Path | None = None) -> list[str]:
    """Retired with the 2026-09-16 restructure (the outputs/ era ended by
    operator decision — workspace wiped, nothing to migrate). Kept as a
    documented no-op for external callers. Never raises."""
    return []


_LEGACY_ROOT_NAMES = ("medusa_agent",)
_LEGACY_INNER_NAMES = ("medusa_agent",)


def ensure_workspace_layout(base_dir: Path | None = None, workspace_dir: Path | None = None) -> bool:
    """Enforce the repo/workspace symlink contract (legacy inner path ->
    workspace). Idempotent; returns True when something changed."""
    base = Path(base_dir) if base_dir else BASE_DIR
    root = Path(workspace_dir) if workspace_dir else WORKSPACE_DIR
    inner = base / "suijin_agent"
    migrated = False

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

    for legacy in _LEGACY_INNER_NAMES:
        legacy_inner = base / legacy
        if legacy_inner.is_symlink():
            legacy_inner.unlink()
        elif legacy_inner.exists():
            root.mkdir(parents=True, exist_ok=True)
            _merge_tree(legacy_inner, root)
            legacy_inner.rmdir()
            migrated = True

    ensure_global_layout()

    if inner.is_symlink():
        return migrated
    if inner.exists():
        root.mkdir(parents=True, exist_ok=True)
        _merge_tree(inner, root)
        inner.rmdir()
    try:
        inner.symlink_to(os.path.relpath(root, base))
        return True
    except OSError:
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
