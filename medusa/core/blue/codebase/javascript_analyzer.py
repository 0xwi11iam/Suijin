"""JavaScript route extraction — Express, Next.js, Remix."""
from __future__ import annotations

import re
from pathlib import Path


def extract_js_routes(root: Path) -> list:
    endpoints = []
    for jsfile in root.rglob("*.js"):
        if any(s in str(jsfile) for s in ["node_modules", ".next", "dist", "build"]):
            continue
        try:
            source = jsfile.read_text(errors="ignore")
            for m in re.finditer(r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']', source):
                endpoints.append({"method":m.group(1).upper(),"path":m.group(2),"file":str(jsfile),"line":source[:m.start()].count("\n")+1,"framework":"express"})
        except Exception:
            continue
    return endpoints
