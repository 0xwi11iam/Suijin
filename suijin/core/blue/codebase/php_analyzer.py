"""PHP route extraction — Laravel."""

from __future__ import annotations

import re
from pathlib import Path


def extract_php_routes(root: Path) -> list:
    endpoints = []
    for pf in root.rglob("*.php"):
        if any(p in pf.parts for p in ("vendor", "cache")) or pf.name.startswith("test"):
            continue
        try:
            source = pf.read_text(errors="ignore")
            for m in re.finditer(r"Route::(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]", source):
                endpoints.append(
                    {
                        "method": m.group(1).upper(),
                        "path": m.group(2),
                        "file": str(pf),
                        "line": source[: m.start()].count("\n") + 1,
                        "framework": "laravel",
                    }
                )
        except Exception:
            continue
    return endpoints
