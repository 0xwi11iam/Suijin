"""
suijin/mcp_server.py — Suijin MCP sidecar (zero-dependency).

Exposes the full Python Suijin backend to any MCP client over the Model
Context Protocol (stdio transport, newline-delimited JSON-RPC 2.0). Optional
headless bridge; the primary interface is the classic Rich TUI (main.py).

Tools exposed:
  suijin_tool       — generic dispatch: all 85 tools via route_tool()
  execute_terminal  — shell execution with guardrails (dispatch layer)
  suijin_detect     — blue team pattern detector on a request dict
  suijin_kg_attacker — blue team knowledge graph: attacker history
  suijin_status     — engine version, tool count, mode info

No third-party MCP package required — the protocol surface used here
(initialize / tools/list / tools/call / ping) is small and stable.
"""

import inspect
import json
import os
import sys

# Ensure repo root is importable regardless of launch cwd (same pattern as main.py).
# lib/console/modules/suijin -> repo root is FIVE dirnames up.
_pkg_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "suijin"
# Single source of truth: suijin/version.json (via the package __init__).
# This used to be a hardcoded "2.3.0-beta" that drifted for five releases.
SERVER_VERSION = None  # resolved lazily


def _server_version():
    v = globals().get("SERVER_VERSION")
    if v is not None:
        return v
    from suijin import __version__ as _v

    return _v


# Load the module packs (vendored suijin/modules/* + ~/.suijin/modules/*) so every backend tool
# is dispatchable. Idempotent; must run before building the tool registry.
def _module_tools():
    from suijin.modules.loader import get_module_tools

    return get_module_tools()


get_module_tools = _module_tools  # lazy seam

# Module-pack discovery moved behind init_runtime() (Phase 0): import-time
# discovery executed every pack's main.py before the server was even asked
# to do anything. The tools/list handler initializes on demand.

# Curated descriptions for the most-used tools. Module tools without a
# docstring fall back to these.
TOOL_DESCRIPTIONS = {
    "nmap_scan": "Port scan with nmap. Returns the exact command run and the raw scan output.",
    "gobuster_dir": "Directory bruteforce with gobuster against a URL. Returns command + discovered paths.",
    "gobuster_dns": "DNS subdomain bruteforce with gobuster. Returns command + found subdomains.",
    "feroxbuster_scan": "Recursive content discovery with feroxbuster. Returns command + discovered paths.",
    "amass_enum": "Subdomain enumeration with amass. Returns command + passive/active results.",
    "sqlmap_scan": "SQL injection detection/exploitation with sqlmap against a URL. Returns command + findings.",
    "hydra_brute": "Credential bruteforce with hydra against a service. Returns command + working credentials if found.",
    "john_crack": "Offline password cracking with john against a hash file.",
    "search_cve": "Search CVE/NVD intelligence for software and version.",
    "http_request": "Send a raw HTTP request (method, url, headers, body) and return the response.",
    "curl_request": "Fetch a URL with curl. Returns the command run and response.",
    "sslscan_check": "TLS configuration scan with sslscan. Returns command + protocol/cipher findings.",
    "msf_run": "Run a Metasploit module through the local msfrpcd.",
    "msf_command": "Send a raw command to the local Metasploit RPC daemon.",
    "msf_sessions": "List or interact with Metasploit sessions.",
    "write_note": "Write a structured note to the engagement notes directory.",
    "record_finding": "Record a finding (vulnerability, rule, evidence) into the knowledge graph.",
    "check_knowledge": "Check the knowledge graph for what is known about a target or payload.",
    "claim_flag": "Claim a capture-the-flag objective.",
    "apply_patch": "Apply a remediation patch for a vulnerability class.",
    "search_kb": "BM25 full-text search the local security knowledge base (HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP, SecLists). Optional 'source:<name>' in the keyword scopes to one source (e.g. 'source:gtfobins awk sudo'); 'limit' 1-20. Use before attacking: techniques, payload syntax, living-off-the-land binaries, wordlist names.",
    "suggest_exploit": "Offline exploit leads for a fingerprinted service: GTFOBins privesc page + HackTricks + PayloadsAllTheThings hits. Args: service, version?",
    "find_wordlist": "Find SecLists wordlists by keyword and extract them into suijin_agent/wordlists/. Args: keyword, extract?",
    "extract_payloads": "Extract runnable code blocks from KB docs into suijin_agent/payloads/. Args: keyword, max_payloads?",
    "kb_stats": "Knowledge base inventory: per-source doc counts, build age, failed sources.",
    "wordlist_tool": "Merge/dedupe/length-filter wordlists into suijin_agent/wordlists/. Args: action(dedupe|merge|filter), files[], out?, min_len?, max_len?",
    "mine_failures": "Cluster failure_db.json entries into technique/reason patterns to avoid. Args: max_clusters?",
    "anonymize_report": "Scrub IPs/emails/tokens/keys from a report into suijin_agent/reports/anonymized/. Args: file_path.",
    "generate_report": "Generate a structured engagement report from the trace.",
    "attack_tree": "Analyze an attack trace JSON into an attack tree.",
    "job_status": "Check a background job's status.",
    "job_output": "Fetch a background job's output.",
    "job_list": "List all background jobs.",
    "job_cancel": "Cancel a background job.",
    "web_search": "Web search for the query.",
    "payload_generate": "Generate a payload for a vulnerability class.",
    "diff_response": "Compare baseline vs injected HTTP responses.",
    "rate_limit_check": "Check a rate limit on an endpoint.",
    "rate_limit_all": "Check all rate limits.",
    "read_file": "Read a workspace file.",
    "write_file": "Write a workspace file.",
    "execute_terminal": "Execute a shell command with guardrails. Returns the command run and its output.",
}


def _fallback_desc(name: str) -> str:
    return TOOL_DESCRIPTIONS.get(name, f"Suijin backend tool: {name}")


def _py_type_to_json(ann) -> str:
    if ann in (str, int, float, bool, list, tuple, dict):
        return {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            tuple: "array",
            dict: "object",
        }[ann]
    return "string"


def _schema_from_signature(func, name: str) -> dict:
    """Build an MCP inputSchema from a Python callable's signature."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        sig = None
    if sig is None or any(
        p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL) for p in sig.parameters.values()
    ):
        return {
            "type": "object",
            "properties": {"args": {"type": "object", "description": f"Arguments for {name}, keyed by parameter name"}},
        }
    props = {}
    required = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls", "config", "ctx"):
            continue
        prop = {
            "type": _py_type_to_json(param.annotation) if param.annotation is not inspect.Parameter.empty else "string"
        }
        if param.default is not inspect.Parameter.empty:
            try:
                if param.default is not None:
                    json.dumps(param.default)
                    prop["default"] = param.default
            except TypeError:
                pass
        else:
            required.append(pname)
        props[pname] = prop
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _tool_description(func, name: str) -> str:
    doc = (getattr(func, "__doc__", "") or "").strip()
    if doc:
        first = doc.splitlines()[0].strip()
        if first:
            return first
    return _fallback_desc(name)


SPECIAL_TOOL_NAMES = {"execute_terminal", "suijin_detect", "suijin_kg_attacker", "suijin_status"}


def _build_backend_tools():
    """One MCP tool per backend tool, with a real name and schema."""
    from suijin.modules.tools.lib.dispatch import list_route_tools

    module_tools = get_module_tools()
    tools = []
    seen = set(SPECIAL_TOOL_NAMES) | {"suijin_tool"}
    for name in list_route_tools():
        if name in seen:
            continue
        seen.add(name)
        func = module_tools.get(name)
        if func is not None:
            tools.append(
                {
                    "name": name,
                    "description": _tool_description(func, name),
                    "inputSchema": _schema_from_signature(func, name),
                }
            )
        else:
            tools.append(
                {
                    "name": name,
                    "description": _fallback_desc(name),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "args": {"type": "object", "description": "Tool arguments keyed by parameter name"}
                        },
                    },
                }
            )
    return tools


def _make_route_handler(name: str):
    def handler(args):
        from suijin.modules.tools.lib.dispatch import route_tool

        result = route_tool(name, args or {}, {})
        header = (
            f"**[suijin] tool: {name}**\n"
            f"**[suijin] args:** `{json.dumps(args or {}, default=str)}`\n\n"
            f"```text\n{result}\n```"
        )
        return header

    return handler


# Core (always-present) MCP tools. Backend tools are appended LAZILY on
# first tools/list / tools/call — building them at import time would run
# module-pack discovery before init_runtime() has been called (Phase 0).
TOOLS = [
    {
        "name": "suijin_tool",
        "description": "Fallback dispatcher: run any Suijin backend tool by name "
        "(tool_name + args dict). Prefer the individually exposed tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Suijin tool name"},
                "args": {"type": "object", "description": "Tool arguments keyed by parameter name"},
            },
            "required": ["tool_name"],
        },
    },
    {
        "name": "execute_terminal",
        "description": "Execute a shell command through the Suijin dispatch layer. "
        "Guardrails block destructive commands; returns the command run and its output.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout seconds (default 30)"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "suijin_detect",
        "description": "Run the blue team pre-AI attack pattern detector on a request. "
        "Returns score + matched patterns (SQLi, XSS, SSRF, ...).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "object",
                    "description": "Request dict: method, path, body, ip, user_agent, query, headers",
                },
            },
            "required": ["request"],
        },
    },
    {
        "name": "suijin_kg_attacker",
        "description": "Query the blue team knowledge graph for an attacker's history "
        "(flags, attacks, defenses deployed against them).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Attacker IP address"},
            },
            "required": ["ip"],
        },
    },
    {
        "name": "suijin_status",
        "description": "Engine status: version, backend health, tool count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_full_tools_cache: list | None = None


def _full_tools() -> list:
    """Core tools + one entry per backend tool (built once, on demand)."""
    global _full_tools_cache
    if _full_tools_cache is None:
        from suijin.modules.platform.lib.runtime import init_runtime

        init_runtime()
        _full_tools_cache = TOOLS + _build_backend_tools()
    return _full_tools_cache


def _jsonrpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _jsonrpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _text_content(text, is_error=False):
    payload = {"content": [{"type": "text", "text": str(text)}]}
    if is_error:
        payload["isError"] = True
    return payload


# ── Tool implementations ──────────────────────────────────────────────


def tool_suijin_tool(args):
    tool_name = args.get("tool_name", "")
    tool_args = args.get("args") or {}
    from suijin.modules.tools.lib.dispatch import route_tool

    return route_tool(tool_name, tool_args, {})


def tool_execute_terminal(args):
    from suijin.modules.tools.lib.dispatch import execute_terminal

    return execute_terminal(args.get("cmd", ""), timeout=int(args.get("timeout", 30)))


def tool_suijin_detect(args):
    from suijin.modules.blueteam.lib.blue.tui.feed import _detect_obvious_attack

    result = _detect_obvious_attack(args.get("request") or {})
    return json.dumps(result, indent=2)


def tool_suijin_kg_attacker(args):
    from suijin.modules.blueteam.lib.blue.knowledge_graph import get_kg

    return json.dumps(get_kg().get_attacker_history(args.get("ip", "")), indent=2, default=str)


def tool_suijin_status(args):
    from suijin.modules.platform.lib.constants import BLUE_LAB_PORT, PROXY_DEFAULT_PORT
    from suijin.modules.tools.lib.dispatch import list_route_tools

    tools = list_route_tools()
    return json.dumps(
        {
            "server": SERVER_NAME,
            "version": _server_version(),
            "protocol": PROTOCOL_VERSION,
            "blue_lab_port": BLUE_LAB_PORT,
            "proxy_default_port": PROXY_DEFAULT_PORT,
            "tool_count": len(tools),
            "tools_sample": tools[:10],
        },
        indent=2,
    )


TOOL_HANDLERS = {
    "suijin_tool": tool_suijin_tool,
    "execute_terminal": tool_execute_terminal,
    "suijin_detect": tool_suijin_detect,
    "suijin_kg_attacker": tool_suijin_kg_attacker,
    "suijin_status": tool_suijin_status,
}
# Backend-tool handlers register lazily alongside _full_tools() so pack
# callables exist before any tools/call can name them.
for _entry in TOOLS:
    _name = _entry["name"]
    if _name not in TOOL_HANDLERS:
        TOOL_HANDLERS[_name] = _make_route_handler(_name)


def _all_handlers() -> dict:
    """Core handlers + backend-tool handlers (registered once, on demand)."""
    if len(TOOL_HANDLERS) == len(TOOLS):
        for _entry in _full_tools():
            _name = _entry["name"]
            if _name not in TOOL_HANDLERS:
                TOOL_HANDLERS[_name] = _make_route_handler(_name)
    return TOOL_HANDLERS


def handle_message(msg):
    method = msg.get("method")
    request_id = msg.get("id")

    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
            },
        )

    if method == "notifications/initialized":
        return None  # notification — no response

    if method == "ping":
        return _jsonrpc_result(request_id, {})

    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": _full_tools()})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        handler = _all_handlers().get(name)
        if handler is None:
            return _jsonrpc_result(request_id, _text_content(f"Unknown tool: {name}", is_error=True))
        try:
            result = handler(arguments)
            return _jsonrpc_result(request_id, _text_content(result))
        except Exception as e:
            import traceback

            traceback.print_exc(file=sys.stderr)
            return _jsonrpc_result(request_id, _text_content(f"Tool error: {e}", is_error=True))

    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


def main():
    from suijin.modules.platform.lib.runtime import init_runtime

    init_runtime()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
