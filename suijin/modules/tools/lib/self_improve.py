"""
Self-improvement tools — lets the agent modify its own skills, prompts,
and tool implementations to become more effective over time.

CRITICAL: These tools give the agent the power to rewrite itself.
This is intentional — a creative agent needs to be able to improve.

Every edit_skill write snapshots the previous version into
suijin_agent/skill_history/<name>/ so `suijin skills rollback` can undo
any self-modification.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]  # suijin/ package
SKILLS_DIR = None  # optional override; derived from BASE_DIR when unset


def _skills_dir():
    """Skills home (honours patched BASE_DIR / SKILLS_DIR module attrs)."""
    v = globals().get("SKILLS_DIR")
    if v is not None:
        return v
    return globals()["BASE_DIR"] / "modules" / "agent" / "lib" / "skills"


HISTORY_DIR = BASE_DIR.parent / "suijin_agent" / "skill_history"


def _snapshot(skill_path: Path, skill_name: str) -> Path | None:
    """Copy the current skill into timestamped history; returns the snapshot."""
    if not skill_path.exists():
        return None
    hist = HISTORY_DIR / skill_name
    hist.mkdir(parents=True, exist_ok=True)
    snap = hist / f"{time.time_ns()}_{time.strftime('%Y%m%d_%H%M%S')}.py"
    shutil.copy2(skill_path, snap)
    # bound history at 25 revisions per skill
    snaps = sorted(hist.glob("*.py"))
    for old in snaps[:-25]:
        old.unlink()
    return snap


def skill_history(skill_name: str) -> list[Path]:
    """Timestamped snapshots for a skill, oldest first."""
    hist = HISTORY_DIR / skill_name
    return sorted(hist.glob("*.py")) if hist.is_dir() else []


def skill_diff(skill_name: str, rev: str | None = None) -> str:
    """Unified diff of a revision (default latest) vs the live skill."""
    import difflib

    snaps = skill_history(skill_name)
    if not snaps:
        return f"No history for skill '{skill_name}' yet."
    snap = next((s for s in snaps if rev and rev in s.name), snaps[-1])
    live = _skills_dir() / f"{skill_name}.py"
    if not live.exists():
        return f"Live skill '{skill_name}' no longer exists."
    diff = difflib.unified_diff(
        snap.read_text().splitlines(),
        live.read_text().splitlines(),
        fromfile=f"history/{snap.name}",
        tofile="live",
        lineterm="",
    )
    body = "\n".join(diff)
    return body or f"'{skill_name}' live version is identical to {snap.name}"


def skill_rollback(skill_name: str, rev: str | None = None) -> str:
    """Restore a skill from history (default: previous revision)."""
    snaps = skill_history(skill_name)
    if not snaps:
        return f"No history for skill '{skill_name}' — nothing to roll back."
    snap = next((s for s in snaps if rev and rev in s.name), snaps[-1])
    live = _skills_dir() / f"{skill_name}.py"
    if live.exists():
        _snapshot(live, skill_name)  # snapshot current before overwriting
    shutil.copy2(snap, live)
    return f"Rolled back '{skill_name}' to {snap.name}"


def edit_skill(skill_name: str, new_content: str) -> str:
    """Overwrite an attack skill prompt with improved content.

    The agent can refine its own hacking methodology by updating skill files.
    This is how the agent self-improves — it learns what works and codifies it.

    Args:
        skill_name: Name of the skill file (e.g. 'sql_injection', 'xss').
        new_content: Complete new content for the skill file.

    Returns:
        Confirmation or error message.
    """
    skill_path = _skills_dir() / f"{skill_name}.py"
    if not skill_path.exists():
        # List available skills
        skills_dir = _skills_dir()
        available = [f.stem for f in skills_dir.glob("*.py") if f.stem != "__init__" and f.stem != "loader"]
        return f"Skill '{skill_name}' not found. Available: {', '.join(available)}"

    try:
        # Versioned snapshot of the old version (rollback support)
        snap = _snapshot(skill_path, skill_name)

        # Write new content (preserve the module-level variable name pattern)
        var_name = skill_name.upper() + "_SKILL_PROMPT"
        if f"{var_name} =" not in new_content[:100]:
            new_content = f'{var_name} = """\n{new_content}\n"""\n'

        skill_path.write_text(new_content)
        snap_note = f" snapshot {snap.name}" if snap else ""
        return f"[done] Skill '{skill_name}' updated ({len(new_content)} chars).{snap_note}"
    except Exception as e:
        return f"Error updating skill: {e}"


def write_tool(tool_name: str, code: str) -> str:
    """Create a REAL, immediately-callable tool (self-extension).

    Writes an agent pack to ~/.suijin/modules/<name>/ (the user pack
    root — the vendored tree stays clean) with manifest + main.py, then
    re-scans the loader so the tool is routable on the NEXT tool call.

    The code MUST define: def <tool_name>(**kwargs) -> str
    (routed as f(**args); return 'Error: ...' on failure, never raise).
    Prior versions are snapshotted (rollback = delete the pack dir).
    """
    import json as _json

    safe_name = "".join(c for c in tool_name if c.isalnum() or c == "_").lower()
    if not safe_name or safe_name[0].isdigit():
        return "Error: invalid tool name — lowercase alphanumeric + underscores, not starting with a digit."
    if f"def {safe_name}(" not in code:
        return (
            f"Error: the code must define `def {safe_name}(**kwargs) -> str` — "
            "that exact function is what gets routed. Never raise; return 'Error: ...' strings."
        )
    pack_dir = Path.home() / ".suijin" / "modules" / safe_name
    try:
        pack_dir.mkdir(parents=True, exist_ok=True)
        # snapshot prior version (the edit_skill pattern)
        main = pack_dir / "main.py"
        if main.exists():
            hist = pack_dir / "_history"
            hist.mkdir(exist_ok=True)
            shutil.copy2(main, hist / f"{time.time_ns()}.py")
            hist_files = sorted(hist.glob("*.py"))
            for old in hist_files[:-25]:  # bounded, like skill history
                old.unlink(missing_ok=True)
        main.write_text(code, encoding="utf-8")
        (pack_dir / "manifest.json").write_text(
            _json.dumps(
                {"name": safe_name, "author": "agent", "tools": {safe_name: "agent-authored tool"}},
                indent=2,
            ),
            encoding="utf-8",
        )
        # register NOW — discover_modules is idempotent and re-scans everything
        from suijin.modules.loader import discover_modules, get_module_tools

        discover_modules()
        if safe_name not in get_module_tools():
            return (
                f"[warn] Tool '{safe_name}' written to {pack_dir} but did not register "
                "(check the code imports at module top level). It will retry on next boot."
            )
        return (
            f"[done] Tool '{safe_name}' REGISTERED — callable immediately as a tool_name. "
            f"Pack: {pack_dir}. Test it on your next turn."
        )
    except Exception as e:  # noqa: BLE001 — tools return strings
        return f"Error writing tool: {e}"


def list_available_skills() -> str:
    """List all attack skills the agent can edit."""
    skills_dir = _skills_dir()
    files = sorted(f.stem for f in skills_dir.glob("*.py") if f.stem not in ("__init__", "loader"))
    return "Available skills:\n" + "\n".join(f"  - {s}" for s in files)


def list_own_files() -> str:
    """List all code files the agent can read/modify."""
    lines = []
    for subdir in ["skills", "tools", "prompts", "nodes", "helpers", "core"]:
        d = BASE_DIR / subdir
        if d.exists():
            py_files = sorted(f.name for f in d.glob("*.py") if f.stem != "__init__")
            if py_files:
                lines.append(f"\n{subdir}/:")
                lines.extend(f"  {f}" for f in py_files)
    return "Self-modifiable files:" + "\n".join(lines)
