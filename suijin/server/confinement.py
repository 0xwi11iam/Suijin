"""Write confinement — nothing the agent writes lands outside its
engagement folder.

v1 (tool-layer): every agent-supplied FILESYSTEM WRITE destination is
jailed — absolute paths outside the engagement mirror into ``home/``,
relative paths resolve against ``home/``. Host shell READS stay free.

Honest scope: shell-level redirection inside execute_terminal
(``curl -o /etc/x``) needs the executor's process sandbox (the next
stage); this jail covers the structured file-writing tools. The
sanctioned operator-config surfaces (skills library, prompt.md via
`suijin prompt`) are allowlisted seams, not agent work product.
"""

from __future__ import annotations

from pathlib import Path

#: operator-owned surfaces the agent may legitimately write (by design,
#: not engagement work product)
_ALLOWLIST_SUBPATHS = (
    ("skills",),  # edit_skill — the self-improvement library
    ("prompts",),  # prompt.md machinery
)


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
