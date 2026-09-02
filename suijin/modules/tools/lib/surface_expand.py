"""surface_expand — the sibling/derived-endpoint enumeration (Wave 4).

The field review's missed pivot: the router accepted arbitrary modal
names and the agent never enumerated the siblings. Given a URL pattern
(with a {name} placeholder or a trailing segment), generate + probe the
sibling candidates from pattern-aware wordlists (observed naming
conventions, common portal nouns) — paced, through the governed engine,
returns what EXISTS (status != baseline-404).
"""

from __future__ import annotations

import json

_WORDLIST = [
    # portal/app nouns (the review's exact misses first)
    "settings", "forgot-password", "reset-password", "profile", "account",
    "admin", "login", "logout", "signup", "register", "help", "support",
    "dashboard", "payments", "billing", "orders", "uploads", "export",
    "search", "users", "api", "internal", "debug", "config", "backup",
    "v1", "v2", "v3", "health", "status", "graphql", "metrics",
]

_DERIVATIONS = [
    ("{name}.json", lambda n: f"{n}.json"),
    ("{name}.bak", lambda n: f"{n}.bak"),
    ("_{name}", lambda n: f"_{n}"),
    ("{name}s", lambda n: f"{n}s"),
]


def surface_expand(url: str = "", names: list | None = None, include_derived: bool = True,
                   timeout: int = 15, allow_internal: bool = False) -> str:
    """Enumerate sibling endpoints for a pattern URL. {name} placeholder
    or the last path segment is replaced with each candidate; existing
    (non-404) hits return ranked. Paced through the governed engine."""
    try:
        from urllib.parse import urlsplit, urlunsplit

        from suijin.modules.tools.lib.http_replay import _scope_guard, _send

        if not url:
            return "Error: url required (use {name} where the sibling varies, or the parent of a known route)"
        if not allow_internal:
            guard = _scope_guard(url, None)
            if guard:
                return f"Error: {guard}"
        parts = urlsplit(url)
        path = parts.path
        candidates = [str(n) for n in (names or [])] or list(_WORDLIST)
        if include_derived:
            extra = []
            for n in candidates[:20]:
                for _, fn in _DERIVATIONS:
                    extra.append(fn(n))
            candidates += extra
        candidates = candidates[:60]

        results = []
        if "{name}" in path:
            tmpl = path
        else:
            seg = path.rstrip("/").split("/")[-1]
            tmpl = path.rstrip("/")[: -len(seg)] + "{name}" if seg else path
        for name in candidates:
            p = tmpl.replace("{name}", name)
            u = urlunsplit((parts.scheme, parts.netloc, p, parts.query, parts.fragment))
            res = _send({"method": "GET", "url": u, "headers": {}, "body": "", "cookies": ""}, timeout=timeout)
            st = res.get("status")
            if st and st != 404:
                results.append({"path": p, "status": st, "len": res.get("length", 0)})
        if not results:
            return json.dumps({"pattern": tmpl, "probed": len(candidates), "existing": [],
                               "note": "no siblings found — the router/pattern may be unique"}, indent=2)
        ranked = sorted(results, key=lambda r: (r["status"] != 200, -r["len"]))
        return json.dumps({"pattern": tmpl, "probed": len(candidates), "existing": ranked[:15]}, indent=2)[:4000]
    except Exception as e:  # noqa: BLE001
        return f"Error: surface_expand failed: {e}"
