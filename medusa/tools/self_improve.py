"""
Self-improvement tools — lets the agent modify its own skills, prompts,
and tool implementations to become more effective over time.

CRITICAL: These tools give the agent the power to rewrite itself.
This is intentional — a creative agent needs to be able to improve.
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # medusa/ root


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
    skill_path = BASE_DIR / "skills" / f"{skill_name}.py"
    if not skill_path.exists():
        # List available skills
        skills_dir = BASE_DIR / "skills"
        available = [f.stem for f in skills_dir.glob("*.py") if f.stem != "__init__" and f.stem != "loader"]
        return f"Skill '{skill_name}' not found. Available: {', '.join(available)}"

    try:
        # Backup old version
        backup_path = skill_path.with_suffix(".py.bak")
        if skill_path.exists():
            backup_path.write_text(skill_path.read_text())

        # Write new content (preserve the module-level variable name pattern)
        var_name = skill_name.upper() + "_SKILL_PROMPT"
        if f"{var_name} =" not in new_content[:100]:
            new_content = f'{var_name} = """\n{new_content}\n"""\n'

        skill_path.write_text(new_content)
        return f"✅ Skill '{skill_name}' updated ({len(new_content)} chars). Backup saved to {backup_path.name}"
    except Exception as e:
        return f"Error updating skill: {e}"


def write_tool(tool_name: str, code: str) -> str:
    """Create or update a tool implementation.

    The agent can write new tools in Python to extend its capabilities.
    Tools are saved to medusa/tools/ and loaded automatically.

    Args:
        tool_name: Python-safe name for the tool (e.g. 'custom_scanner').
        code: Complete Python code for the tool.

    Returns:
        Confirmation or error.
    """
    safe_name = "".join(c for c in tool_name if c.isalnum() or c == "_").lower()
    if not safe_name:
        return "Invalid tool name — use alphanumeric characters and underscores."

    tool_path = BASE_DIR / "tools" / f"{safe_name}.py"
    try:
        tool_path.write_text(code)
        return f"✅ Tool '{safe_name}' written to tools/{safe_name}.py ({len(code)} chars)."
    except Exception as e:
        return f"Error writing tool: {e}"


def list_available_skills() -> str:
    """List all attack skills the agent can edit."""
    skills_dir = BASE_DIR / "skills"
    files = sorted(f.stem for f in skills_dir.glob("*.py")
                   if f.stem not in ("__init__", "loader"))
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
