"""Workspace path management — the ONE owner of every path.

The v7.2 CLEAN LAYOUT (operator ruling 2026-10-12: "inside suijin agent
there should only be /config and /engagements"):

    ~/.suijin/
    ├── config/                     # WORKSPACE_DIR — non-engagement paths
    │   ├── profiles/<name>/…       #   operator-owned personas
    │   ├── skills/                 #   SKILL.md library
    │   ├── packs/                  #   .sja pack exports
    │   ├── prompts/, SOUL.md, …    #   prompt content
    │   └── reflex-shadow.jsonl     #   decision-training corpus
    ├── engagements/<stamp>_<slug>/ # generated at start — the ONLY other
    │   ├── home/                   #   agent-owned root
    │   │   ├── .notes/ exploits/ audit_trails/ dossiers/
    │   │   ├── toollogs/ reports/ log/ state/ memory/ sessions/
    ├── logs/                       # run-level crash/engage logs
    ├── caches/                     # models.dev, ms-playwright (machine)
    └── venv/ keys/ mesh/ repo/     # install-level (not agent content)

No agent or tool writes ANYWHERE at ~/.suijin except config/ and
engagements/ (logs/ + caches/ are system dirs like venv/). The old
~/.suijin/workspace layout is retired; a one-time migration folds its
surviving content in and parks the husk at workspace.retired-<stamp>.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]  # suijin/ root
PROJECT_DIR = BASE_DIR.parent  # repo root


def _resolve_workspace() -> Path:
    """The ONE canonical workspace (= the config dir, v7.2 clean layout).

    Resolution order:
      1. SUIJIN_WORKSPACE env (explicit override — tests + operators)
      2. ~/.suijin/config — the durable home (created on first boot;
         survives repo re-clones, reinstalls, wipes)
      3. repo-local suijin_agent (bare source checkouts with no
         ~/.suijin at all) — if it is a symlink, follow it
    """
    env = os.environ.get("SUIJIN_WORKSPACE")
    if env:
        return Path(env).expanduser()
    cfg = Path.home() / ".suijin" / "config"
    if (Path.home() / ".suijin").is_dir() or cfg.is_dir():
        return cfg  # installed machine — always the clean layout
    local = PROJECT_DIR / "suijin_agent"
    if local.is_symlink():
        try:
            resolved = local.resolve()
            if resolved.is_dir() and not resolved.is_symlink():
                return resolved
        except OSError:
            pass
    return cfg  # default even bare: first boot creates the clean layout


WORKSPACE_DIR = _resolve_workspace()


def base_dir() -> Path:
    """The clean-layout parent: ~/.suijin in production, or the sandbox
    root when SUIJIN_WORKSPACE points inside one (tests). config/,
    engagements/, logs/ and caches/ hang off it. Nothing the agent
    writes lands directly here."""
    return WORKSPACE_DIR.parent


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

#: workspace-global: operator-owned knowledge + persona libraries —
#: everything else is engagement-scoped or system-level (logs/, caches/)
GLOBAL_DIRS = (
    "profiles",  # <name>/{SOUL.md, rules.md, config.json} — operator-owned
    "skills",  # the SKILL.md library (imported knowledge, like profiles)
    "packs",  # .sja pack exports (module install artifacts)
    "bugscope",  # program scope knowledge (bb-scope pulls — cross-engagement)
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
    """Create the clean-layout skeleton (idempotent, never raises):
    config/{profiles,skills,packs} + base/engagements, base/logs,
    base/caches — and fold any pre-v7.2 ~/.suijin/workspace in."""
    for name in GLOBAL_DIRS:
        _ensure(WORKSPACE_DIR / name)
    _ensure(engagements_root())
    _ensure(base_dir() / "logs")
    _ensure(caches_dir())
    _migrate_legacy_workspace()


def engagements_root() -> Path:
    """ALL engagement folders live here (~/.suijin/engagements)."""
    return base_dir() / "engagements"


def caches_dir() -> Path:
    """Machine-level caches (models.dev.json, ms-playwright) — never
    inside config/ or an engagement."""
    return base_dir() / "caches"


def audit_dir() -> Path:
    """Where CLI-verb invocation audits go: the LIVE engagement's
    audit_trails when a run is active, else config/audit — an idle verb
    (doctor, version) must never conjure a _default engagement dir."""
    if _CURRENT_ENGAGEMENT is not None:
        return _ensure(_CURRENT_ENGAGEMENT / "audit_trails")
    return _ensure(WORKSPACE_DIR / "audit")


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
    if _CURRENT_ENGAGEMENT is not None:
        return _ensure(_CURRENT_ENGAGEMENT / name)
    return _ensure(WORKSPACE_DIR / "artifacts" / name)


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
    eng_root = engagements_root()
    base = eng_root / f"{stamp}_{_slugify(objective[:60])}"
    # concurrent same-second starts (parallel agent launches) would silently
    # MERGE into one engagement — one audit trail, mixed findings. Uniquify.
    d = base
    n = 2
    while d.exists():
        d = base.with_name(f"{base.name}~{n}")
        n += 1
    d.mkdir(parents=True)
    # LAZY SUBDIRS (operator ruling 2026-10-05): the old layout created 11+
    # empty dirs per engagement before the agent did anything — a workspace
    # of 10 parallel agents was 130 empty directories of noise. Every
    # accessor (notes_dir, exploits_dir, state_dir, ...) already calls
    # _ensure() and creates on first WRITE; set_engagement creates only
    # the two that always receive files immediately (log/ for the
    # engagement logger, state/ for the guidance/scratchpad the first
    # turn reads).
    (d / "log").mkdir(exist_ok=True)
    (d / "state").mkdir(exist_ok=True)
    _CURRENT_ENGAGEMENT = d
    return d


def engagement_dir() -> Path:
    """The current engagement's folder. Before set_engagement boots this
    is the `_default` path — returned WITHOUT creating it (v7.2: an
    idle read must not litter engagements/ with empty husks; write
    accessors _ensure their own subdirs when a real engagement is set).
    """
    return _CURRENT_ENGAGEMENT or engagements_root() / "_default"


def _engaged(sub: str) -> Path:
    """engagement_dir()/sub — created ONLY when an engagement is live.
    Pre-engagement callers get the path without conjuring a _default
    husk (v7.2: reads must not litter; writes belong to live runs)."""
    d = engagement_dir() / sub
    if _CURRENT_ENGAGEMENT is not None:
        _ensure(d)
    return d


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
    if _CURRENT_ENGAGEMENT is not None:
        return _ensure(_CURRENT_ENGAGEMENT / ".notes")
    return _ensure(WORKSPACE_DIR / "artifacts" / ".notes")


def exploits_dir() -> Path:
    """The engagement's exploit catalog root (EXP-NNN/ folders)."""
    if _CURRENT_ENGAGEMENT is not None:
        return _ensure(_CURRENT_ENGAGEMENT / "exploits")
    return _ensure(WORKSPACE_DIR / "artifacts" / "exploits")


def state_dir() -> Path:
    """Per-engagement state files (scratchpad, guidance, questions, .sje)."""
    if _CURRENT_ENGAGEMENT is not None:
        return _ensure(_CURRENT_ENGAGEMENT / "state")
    return _ensure(WORKSPACE_DIR / "artifacts" / "state")


def toollogs_dir() -> Path:
    """Timestamped tool output dumps (nmap_scan_*, execute_terminal_*)."""
    if _CURRENT_ENGAGEMENT is not None:
        return _ensure(_CURRENT_ENGAGEMENT / "toollogs")
    return _ensure(WORKSPACE_DIR / "artifacts" / "toollogs")


def profiles_root() -> Path:
    """File-backed profile folders: <name>/{SOUL.md, rules.md, config.json}."""
    return _ensure(WORKSPACE_DIR / "profiles")


def logs_dir() -> Path:
    """Run-level logs: engage_crash.log and friends — base/logs, NEVER
    under config/ or an engagement (the old WORKSPACE_DIR/'outputs'/logs
    and the nested logs/logs are gone with v7.2)."""
    return _ensure(base_dir() / "logs")


def archive_engagement(reason: str = "ended") -> Path | None:
    """Move the engagement folder into engagements/.archive (timestamped,
    immutable) — the .sje bundle inside state/ remains the resume artifact."""
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
    dest = engagements_root() / ".archive" / f"{stamp}_{src.name}_{_slugify(reason)}"
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
            real.relative_to(base_dir().resolve())
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

    # v7.2: the repo-level suijin_agent symlink is RETIRED — the
    # workspace is no longer a single directory (config/ + engagements/
    # + logs/ + caches/ under ~/.suijin). Remove a stale symlink from
    # the old layout; never create a new one.
    if inner.is_symlink():
        with contextlib.suppress(OSError):
            inner.unlink()
        migrated = True
    elif inner.exists():
        root.mkdir(parents=True, exist_ok=True)
        _merge_tree(inner, root)
        inner.rmdir()
        migrated = True
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


def _migrate_legacy_workspace() -> None:
    """One-time (v7.2): fold the retired ~/.suijin/workspace into the
    clean layout. Survivors: profiles/, skills/, prompts/, SOUL.md,
    prompt.md, reflex-shadow.jsonl → config/; caches/ → base/caches;
    engagements/* (minus _default/state husks) → base/engagements. The
    husk itself is parked at workspace.retired-<stamp> for the operator
    to delete — nothing is destroyed here. Idempotent, never raises,
    and never runs when SUIJIN_WORKSPACE is set (tests own layouts)."""
    if os.environ.get("SUIJIN_WORKSPACE"):
        return
    legacy = Path.home() / ".suijin" / "workspace"
    if not legacy.is_dir() or (legacy / ".migrated-v72").exists():
        return
    import shutil as _sh
    from datetime import datetime as _dt

    def _take(name: str, dest: Path) -> None:
        src = legacy / name
        if not src.exists():
            return
        with contextlib.suppress(OSError):
            if src.is_dir():
                _sh.copytree(src, dest / src.name, dirs_exist_ok=True)
            else:
                _sh.copy2(src, dest / src.name)

    for name in ("profiles", "skills", "prompts", "SOUL.md", "prompt.md", "reflex-shadow.jsonl"):
        _take(name, WORKSPACE_DIR)
    _take("caches", base_dir())
    eng_src = legacy / "engagements"
    if eng_src.is_dir():
        for item in list(eng_src.iterdir()):
            if item.name.startswith(".") or item.name in ("_default", "state"):
                continue  # boot-time husks — die with the legacy root
            with contextlib.suppress(OSError):
                _sh.copytree(item, engagements_root() / item.name, dirs_exist_ok=True)
    (legacy / ".migrated-v72").write_text(f"migrated {_dt.now().isoformat()}")
    with contextlib.suppress(OSError):
        legacy.rename(legacy.with_name(f"workspace.retired-{_dt.now():%Y%m%d_%H%M%S}"))
