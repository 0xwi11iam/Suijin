"""AI userland tools — the session environment, action cache, and runtime
tool discovery that make the agent's interface feel like a proper OS.

state:       named variables (like shell vars) — fixes 27 "save this for later" iterations
tools:       categorized quick reference — "what do I have for X?"
help:        runtime man page for any tool — usage, args, examples
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# STATE — persistent named variables (shell variables for the AI)
# ═══════════════════════════════════════════════════════════════════


def _state_path() -> Path:
    from suijin.modules.platform.lib.workspace import state_dir

    return state_dir() / "vars.json"


def _load_vars() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_vars(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1, default=str))


def state(action: str = "list", name: str = "", value: str = "") -> str:
    """Persistent named variables — like shell variables for the agent.

    state("set", "admin_token", "eyJ...") — store a value
    state("get", "admin_token")            — retrieve it
    state("list")                           — show all variables
    state("del", "admin_token")            — delete one
    """
    action = action.lower().strip()
    vars_ = _load_vars()

    if action == "set":
        if not name:
            return 'Usage: state("set", "name", "value")'
        vars_[name] = str(value)[:2000]
        _save_vars(vars_)
        return f'Stored {name} ({len(value)} chars). Retrieve with state("get", "{name}")'

    elif action == "get":
        if not name:
            return 'Usage: state("get", "name")'
        val = vars_.get(name)
        if val is None:
            return f"No variable named '{name}'. Use state(\"list\") to see all."
        return str(val)

    elif action == "del" or action == "delete":
        if name in vars_:
            del vars_[name]
            _save_vars(vars_)
            return f"Deleted {name}"
        return f"No variable named '{name}'"

    elif action == "list":
        if not vars_:
            return 'No variables set. Use state("set", "name", "value") to store tokens, URLs, findings.'
        lines = [f"Variables ({len(vars_)}):"]
        for k, v in sorted(vars_.items()):
            preview = str(v)[:60] + ("..." if len(str(v)) > 60 else "")
            lines.append(f"  {k}: {preview}")
        return "\n".join(lines)

    return 'Usage: state("set"/"get"/"del"/"list", name, value)'


# ═══════════════════════════════════════════════════════════════════
# TOOLS — categorized quick reference
# ═══════════════════════════════════════════════════════════════════

_TOOL_CATEGORIES = {
    "recon": [
        "api_discover",
        "dom_recon",
        "surface_expand",
        "recon_chain",
        "crawl",
        "js_bundle_analyze",
        "google_dork",
        "web_search",
        "check_knowledge",
        "memory_recall",
        "skill_search",
        "skill_load",
    ],
    "attack": [
        "inject_probe",
        "auth_forge",
        "race_test",
        "cred_matrix",
        "param_pollute",
        "graphql_attack",
        "mass_assign",
        "header_enforce",
        "origin_confuse",
        "bypass_403",
        "payload_generator",
        "payload_mutate",
        "dispatch_testers",
        "code_harness",
        "source_map_probe",
    ],
    "exploit": [
        "catalog_exploit",
        "record_finding",
        "evidence_chain",
        "exploit",
        "http_replay",
        "http_replay_raw",
    ],
    "browser": [
        "mcp_browser_goto",
        "mcp_browser_snapshot",
        "mcp_browser_click",
        "mcp_browser_type",
        "mcp_browser_exec",
        "mcp_browser_get_html",
        "mcp_browser_extract",
        "mcp_browser_screenshot",
        "mcp_browser_close",
        "mcp_browser_net_start",
        "mcp_browser_net_stop",
        "mcp_browser_requests",
        "mcp_browser_cookies",
        "mcp_browser_storage",
        "mcp_browser_intercept",
        "mcp_browser_intercept_clear",
        "mcp_browser_tab_open",
        "mcp_browser_tab_switch",
        "mcp_browser_tab_list",
        "mcp_browser_tab_close",
        "mcp_browser_console",
        "mcp_browser_wait",
        "mcp_browser_auth_save",
        "mcp_browser_auth_restore",
        "mcp_browser_shadow",
        "mcp_browser_fingerprint",
    ],
    "network": [
        "http_request",
        "smart_fetch",
        "response_diff",
    ],
    "utility": [
        "jwt_decode",
        "state",
        "tools",
        "help",
        "collect_all",
        "execute_terminal",
        "write_file",
        "read_file",
        "edit_file",
        "safe_exec",
        "search_cve",
        "search_kb",
        "note",
    ],
    "mesh": [
        "mesh_broadcast",
        "mesh_dm",
        "mesh_ask",
        "mesh_reply",
        "mesh_status",
        "mesh_read",
    ],
    "jobs": [
        "job_start",
        "job_status",
        "job_output",
        "job_wait",
        "job_list",
        "collect_all",
    ],
    "fireteam": [
        "deploy_subagent",
        "fireteam_status",
    ],
}


def tools(category: str = "") -> str:
    """Quick categorized tool reference. tools() shows all categories.
    tools('attack') shows only attack tools."""
    from suijin.modules.tools.lib.dispatch import _build_routes

    routes = _build_routes({})
    cat = (category or "").lower().strip()

    if cat and cat in _TOOL_CATEGORIES:
        tool_names = _TOOL_CATEGORIES[cat]
        lines = [f"## {cat.upper()} tools ({len(tool_names)}):"]
        for name in tool_names:
            if name in routes:
                lines.append(f"  {name}")
        return "\n".join(lines)

    lines = [f"All tools ({len(routes)} total), by category:\n"]
    for cat_name, tool_names in _TOOL_CATEGORIES.items():
        available = [t for t in tool_names if t in routes]
        lines.append(
            f"  {cat_name:>12} ({len(available):>2}): {', '.join(available[:6])}{'...' if len(available) > 6 else ''}"
        )

    # uncategorized
    categorized = {t for names in _TOOL_CATEGORIES.values() for t in names}
    other = sorted(set(routes) - categorized)
    if other:
        lines.append(f"  {'other':>12} ({len(other):>2}): {', '.join(other[:8])}{'...' if len(other) > 8 else ''}")

    lines.append("\nUse tools('<category>') for details, help('<tool>') for usage.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# HELP — runtime man page for any tool
# ═══════════════════════════════════════════════════════════════════


def help_tool(name: str = "") -> str:
    """Runtime tool lookup — usage, args, description, related tools.
    help('auth_forge') shows how to use auth_forge.
    help('graphql') shows all graphql-related tools."""
    from suijin.modules.tools.lib.dispatch import _build_routes

    routes = _build_routes({})
    name = (name or "").lower().strip()

    if not name:
        return 'Usage: help("<tool_name>") or help("<pattern>") to search.'

    # exact match
    if name in routes:
        fn = routes[name]
        import inspect

        sig = ""
        doc = ""
        with contextlib.suppress(Exception):
            sig = str(inspect.signature(fn))
        doc = (inspect.getdoc(fn) or "")[:300]

        lines = [f"## {name}"]
        if sig:
            lines.append(f"  signature: {name}{sig}")
        if doc:
            lines.append(f"  {doc}")
        if not sig and not doc:
            lines.append("  (no docs — try the tool with minimal args)")

        # related tools
        related = [t for t in routes if t != name and name.split("_")[0] in t]
        if related:
            lines.append("\n  related: " + ", ".join(related[:5]))
        return "\n".join(lines)

    # pattern match
    matches = [t for t in sorted(routes) if name in t.lower()]
    if matches:
        lines = [f"Tools matching '{name}' ({len(matches)}):"]
        for m in matches[:10]:
            fn = routes[m]
            doc = (inspect.getdoc(fn) or "")[:80] if callable(fn) else ""
            lines.append(f"  {m}: {doc}")
        return "\n".join(lines)

    return f"No tool matching '{name}'. Use tools() to see all available tools."
