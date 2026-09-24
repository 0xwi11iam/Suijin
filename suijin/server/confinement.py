"""Write confinement — nothing the agent writes lands outside its
engagement folder.

v1 (tool-layer): every agent-supplied FILESYSTEM WRITE destination is
jailed — absolute paths outside the engagement mirror into ``home/``,
relative paths resolve against ``home/``. Host shell READS stay free.

v2 (executor-stage): shell-level redirection (``curl -o /etc/x``) is
closed at the PROCESS layer, not by parsing strings. Every shell the
agent drives runs with ``HOME``/``TMPDIR`` pointed INTO the engagement
home, and (when the OS sandbox is present and ``executor_sandbox`` is
on — the default) the command is executed under ``sandbox-exec`` with a
profile that DENIES file writes everywhere except the workspace +
``/tmp`` scratch. Children inherit the sandbox, so the ``/bin/sh -c``
wrapper of a redirect-laden one-liner is confined too.

Honest scope:
- READs are unrestricted; NETWORK is unrestricted (the agent probes
  targets for a living); only file WRITEs are denied outside the
  workspace + /tmp scratch.
- macOS ``sandbox-exec`` is deprecated-but-functional; on systems where
  it is missing the env-level confinement (HOME/TMPDIR) still applies —
  the tool output is the source of truth, never rely on it silently.
- write_file jail (v1) and this executor jail (v2) are separate layers;
  a tool-path that never reaches the OS shell is covered by v1 only.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

#: operator-owned surfaces the agent may legitimately write (by design,
#: not engagement work product)
_ALLOWLIST_SUBPATHS = (
    ("skills",),  # edit_skill — the self-improvement library
    ("prompts",),  # prompt.md machinery
)

#: device paths every sandboxed command needs, even on a watchdog read
_DEVICE_LITERALS = (
    "/dev/null",
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
    "/dev/fd",
)

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _engagement_root():
    from suijin.modules.platform.lib.workspace import engagement_dir

    return engagement_dir()


def _home():
    from suijin.modules.platform.lib.workspace import home_dir

    return home_dir()


def jail_path(raw, root: Path | None = None) -> Path:
    """Map an agent write target INSIDE the engagement.

    - already inside the engagement → unchanged
    - absolute outside → mirrored under home/ (``/etc/x`` →
      ``home/etc/x`` — the write succeeds, harmlessly contained)
    - relative → under home/ (the agent's userland cwd)
    - operator allowlisted surfaces (skills/, prompts/) → unchanged
    """
    p = Path(str(raw)).expanduser()
    root = Path(root) if root else _engagement_root()
    home = root / "home"

    if p.is_absolute():
        try:
            p.relative_to(root)
        except ValueError:
            # outside: check the operator-surface allowlist (workspace paths)
            from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

            try:
                rel = p.relative_to(WORKSPACE_DIR)
                if rel.parts and rel.parts[0] in {a[0] for a in _ALLOWLIST_SUBPATHS}:
                    return p  # sanctioned surface
            except ValueError:
                pass
            # mirror under home/
            return home / str(p).lstrip("/").replace(":", "_")
        return p  # already inside the engagement
    return home / p  # relative paths resolve against the agent's userland


def is_confined(path, root: Path | None = None) -> bool:
    """True when the path sits inside the engagement folder."""
    root = Path(root) if root else _engagement_root()
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ── v2: executor-stage process jail ────────────────────────────────────────


def sandbox_exec_available() -> bool:
    """True when the OS process sandbox is usable on this host."""
    return sys.platform == "darwin" and Path(_SANDBOX_EXEC).is_file()


def _real(path) -> str:
    return str(Path(path).expanduser().resolve())


def _strict() -> bool:
    """executor_sandbox config — ON by default, off only when the operator
    pins the key to false in config.json (file value wins)."""
    try:
        from suijin.modules.platform.lib.config_loader import load_config

        return bool(load_config().get("executor_sandbox", True))
    except Exception:  # noqa: BLE001 — a config hiccup never weakens to off
        return True


def executor_env(root: Path | None = None) -> dict:
    """Confined process environment: HOME + TMPDIR point INTO the
    engagement home so shells, ssh, compilers and caches never touch the
    real user dirs. Always applied (sandbox or not)."""
    from suijin.modules.platform.lib.workspace import home_dir

    env = os.environ.copy()
    home = Path(home_dir()) if root is None else Path(root) / "home"
    tmp = home / "tmp"
    with contextlib.suppress(OSError):
        tmp.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp)
    return env


def _sandbox_profile(root: Path | None = None, workspace=None) -> str:
    """Darwin sandbox-exec profile (one line): allow default, deny every
    file write, then re-allow the workspace, the engagement and /tmp
    scratch (canonical /private/tmp — /tmp itself resolves through the
    symlink so a literal subpath misses) plus the device nodes."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    root = Path(root) if root else _engagement_root()
    ws = Path(workspace) if workspace else WORKSPACE_DIR
    parts = ["(version 1)", "(allow default)", "(deny file-write*)"]
    for sub in (_real(root), _real(ws), "/private/tmp"):
        parts.append(f'(allow file-write* (subpath "{sub}"))')
    for dev in _DEVICE_LITERALS:
        parts.append(f'(allow file-write* (literal "{dev}"))')
    return " ".join(parts)


def wrap_exec(cmd: list, root: Path | None = None, workspace=None, strict: bool | None = None) -> tuple[list, bool]:
    """Prefix ``cmd`` (argv list) with the OS sandbox when it is installed
    and strict jailing is in force. Returns (argv, jailing_active). Any
    deprecation/absence degrades to env-level confinement, never a lie."""
    if strict is None:
        strict = _strict()
    if not (strict and sandbox_exec_available()):
        return list(cmd), False
    profile = _sandbox_profile(root=root, workspace=workspace)
    return [_SANDBOX_EXEC, "-p", profile, *cmd], True
