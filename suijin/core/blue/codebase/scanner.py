"""Codebase scanner — walks tree, dispatches to framework analyzers."""

from __future__ import annotations

import json
from pathlib import Path


def scan_codebase(root_path: str) -> list:
    root = Path(root_path).resolve()
    endpoints = []
    from suijin.core.blue.codebase.java_analyzer import extract_java_routes
    from suijin.core.blue.codebase.javascript_analyzer import extract_js_routes
    from suijin.core.blue.codebase.php_analyzer import extract_php_routes
    from suijin.core.blue.codebase.python_analyzer import extract_python_routes

    endpoints.extend(extract_python_routes(root))
    endpoints.extend(extract_js_routes(root))
    endpoints.extend(extract_java_routes(root))
    endpoints.extend(extract_php_routes(root))
    root_endpoints = root / "suijin_endpoints.json"
    root_endpoints.write_text(json.dumps(endpoints, indent=2))
    return endpoints
