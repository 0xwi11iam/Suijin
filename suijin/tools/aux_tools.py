"""Auxiliary tools: web search, self-improvement, package install."""

from __future__ import annotations


def _web_search(query: str, max_results: int = 5) -> str:
    from suijin.tools.web_search import web_search

    return web_search(query, max_results)


def _edit_skill(skill_name: str, new_content: str) -> str:
    from suijin.tools.self_improve import edit_skill

    return edit_skill(skill_name, new_content)


def _write_tool(tool_name: str, code: str) -> str:
    from suijin.tools.self_improve import write_tool

    return write_tool(tool_name, code)


def _list_skills() -> str:
    from suijin.tools.self_improve import list_available_skills

    return list_available_skills()


def _list_own_files() -> str:
    from suijin.tools.self_improve import list_own_files

    return list_own_files()


def _pip_install(package: str) -> str:
    """Install a Python package for the agent to use. Requires confirmation."""
    if not package or not package.strip():
        return "Error: No package specified."
    safe = package.strip().split()[0]  # Only take first word for safety
    dangerous = {"os", "sys", "subprocess", "shutil", "importlib", "__builtins__"}
    if safe.lower() in dangerous:
        return f"Cannot install system module: {safe}"
    try:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", safe],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return f"Installed {safe}\n{result.stdout[-500:]}"
        return f"Failed to install {safe}\n{result.stderr[-500:]}"
    except Exception as e:
        return f"pip install error: {e}"
