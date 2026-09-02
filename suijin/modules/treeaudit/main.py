"""tree_map — red-team source tree audit: routes × auth × raw SQL.

Bridges the blue-side codebase analyzers (python/js/java/php route
extractors, auth mapper, SQL extractor — complete and tested, previously
defense-only) into an attack-surface map: every endpoint with its auth
classification, every raw SQL query, ranked so the unauthenticated +
raw-SQL surfaces come first.
"""

from __future__ import annotations


def tree_map(root: str = "") -> str:
    """Map a source tree's attack surface: endpoints, auth, raw SQL — ranked."""
    from pathlib import Path

    if not root:
        return "Error: root required (path to the application source)"
    p = Path(root).expanduser()
    if not p.is_dir():
        return f"Error: {p} is not a directory"
    import json

    from suijin.modules.blueteam.lib.blue.codebase.auth_mapper import map_auth
    from suijin.modules.blueteam.lib.blue.codebase.scanner import (
        extract_java_routes,
        extract_js_routes,
        extract_php_routes,
        extract_python_routes,
    )

    endpoints = []
    for fn in (extract_python_routes, extract_js_routes, extract_java_routes, extract_php_routes):
        try:
            endpoints.extend(fn(p))
        except Exception:  # noqa: BLE001 — one language failing is not the map
            continue
    if not endpoints:
        return (
            f"No routes found under {p} (supported: Flask/Django/FastAPI, Express, "
            "Spring, Laravel). If this is a different framework, use sink_grep for "
            "dangerous-call hunting instead."
        )
    try:
        endpoints = map_auth(p, endpoints)
    except Exception:  # noqa: BLE001
        for ep in endpoints:
            ep.setdefault("auth", "unknown")

    from suijin.modules.blueteam.lib.blue.codebase.sql_extractor import extract_sql_queries

    try:
        queries = extract_sql_queries(p)
    except Exception:  # noqa: BLE001
        queries = []

    unauth = [e for e in endpoints if e.get("auth") in ("none", "public", "unknown")]
    authed = [e for e in endpoints if e.get("auth") == "authenticated"]
    raw_sql = [q for q in queries if not q.get("parameterized")]
    param_sql = [q for q in queries if q.get("parameterized")]

    def _ep_line(e):
        loc = ""
        if e.get("file"):
            f = str(e["file"]).rsplit("/", 1)[-1]
            loc = f" ({f}:{e.get('line', '?')})"
        return f"  {e.get('method', 'GET'):6} {e.get('path', '?')}{loc}"

    lines = [
        f"attack-surface map: {p}",
        f"endpoints: {len(endpoints)} total — {len(unauth)} unauth'd, {len(authed)} auth'd",
        "",
        "== UNAUTHENTICATED SURFACES (attack these first) ==",
        *(_ep_line(e) for e in unauth[:40]),
    ]
    if authed:
        lines += [
            "",
            f"== authenticated ({len(authed)}) — creds/bypass targets ==",
            *(_ep_line(e) for e in authed[:20]),
        ]
    lines += ["", f"== SQL: {len(raw_sql)} RAW (injection candidates), {len(param_sql)} parameterized =="]
    for q in raw_sql[:20]:
        f = str(q["file"]).rsplit("/", 1)[-1]
        lines.append(f"  {f}:{q['line']}  {q['sql'][:90]}")
    lines += [
        "",
        "next: hit the unauthenticated raw-SQL endpoints with sql injection "
        "payloads first (http_request), then sink_grep the tree for eval/exec/"
        "deserialize sinks near these routes.",
    ]
    return "\n".join(lines)
