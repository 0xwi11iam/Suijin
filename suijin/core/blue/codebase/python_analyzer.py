"""Python route extraction — Flask, Django, FastAPI."""

from __future__ import annotations

import re
from pathlib import Path


def _is_noise(pyfile: Path) -> bool:
    """True for cache/venv dirs anywhere and test FILES (by name part).

    Checking the whole path string meant a project living under any dir
    containing 'test_' (e.g. pytest tmp dirs, 'contest_app') lost every
    route — exclusions must match path PARTS, not substrings.
    """
    parts = pyfile.parts
    if any(p in parts for p in ("__pycache__", ".venv", "venv", "node_modules")):
        return True
    return pyfile.name.startswith("test_")


def extract_python_routes(root: Path) -> list:
    endpoints = []
    for pyfile in root.rglob("*.py"):
        if _is_noise(pyfile):
            continue
        try:
            source = pyfile.read_text(errors="ignore")
            for m in re.finditer(r'@(?:\w+\.)?route\s*\(\s*["\']([^"\']+)["\']', source):
                ep = {
                    "method": "GET",
                    "path": m.group(1),
                    "file": str(pyfile),
                    "line": source[: m.start()].count("\n") + 1,
                    "framework": "flask",
                }
                endpoints.append(ep)
            for m in re.finditer(r'@(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', source):
                endpoints.append(
                    {
                        "method": m.group(1).upper(),
                        "path": m.group(2),
                        "file": str(pyfile),
                        "line": source[: m.start()].count("\n") + 1,
                        "framework": "fastapi",
                    }
                )
            for m in re.finditer(r'path\s*\(\s*["\']([^"\']+)["\']\s*,\s*(\w+)', source):
                endpoints.append(
                    {
                        "method": "ANY",
                        "path": m.group(1),
                        "file": str(pyfile),
                        "line": source[: m.start()].count("\n") + 1,
                        "framework": "django",
                        "view": m.group(2),
                    }
                )
        except Exception:
            continue
    return endpoints
