"""
Hot-Reload Skills — agent can edit skill files and changes take effect immediately.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_last_modified = {}


def check_skill_updates() -> list:
    """Check if any skill files were modified since last check.
    Returns list of updated skill names.
    """
    global _last_modified
    updated = []
    for f in SKILLS_DIR.glob("*.py"):
        if f.name.startswith("__"):
            continue
        mtime = os.path.getmtime(f)
        if _last_modified.get(f.name, 0) < mtime:
            _last_modified[f.name] = mtime
            updated.append(f.stem)
    return updated


def reload_skills(verbose: bool = False):
    """Reload all skill modules from disk. Call after editing skill files."""
    updated = check_skill_updates()
    if not updated:
        if verbose:
            print("No skill changes detected.")
        return []

    reloaded = []
    for name in updated:
        try:
            module_name = f"medusa.skills.{name}"
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            reloaded.append(name)
        except Exception as e:
            print(f"Failed to reload {name}: {e}")

    # Reload the loader to pick up new SKILL_MAP entries
    try:
        if "medusa.skills.loader" in sys.modules:
            importlib.reload(sys.modules["medusa.skills.loader"])
    except Exception as e:
        import logging

        logging.getLogger("medusa").warning(f"Skill reload failed: {e}")
        pass

    if verbose and reloaded:
        print(f"Reloaded skills: {', '.join(reloaded)}")
    return reloaded


def edit_skill_file(skill_name: str, new_content: str) -> bool:
    """Edit a skill file on disk. Changes take effect on next reload/reload_skills call."""
    skill_file = SKILLS_DIR / f"{skill_name}.py"
    if not skill_file.exists():
        return False
    skill_file.write_text(new_content)
    return True
