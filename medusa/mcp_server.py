"""
medusa/mcp_server.py — Medusa MCP sidecar (zero-dependency).

Exposes the full Python Medusa backend to the Medusa TUI over the Model
Context Protocol (stdio transport, newline-delimited JSON-RPC 2.0).

Tools exposed:
  medusa_tool       — generic dispatch: all 85 tools via route_tool()
  execute_terminal  — shell execution with guardrails (dispatch layer)
  medusa_detect     — blue team pattern detector on a request dict
  medusa_kg_attacker — blue team knowledge graph: attacker history
  medusa_status     — engine version, tool count, mode info

No third-party MCP package required — the protocol surface used here
(initialize / tools/list / tools/call / ping) is small and stable.
"""
import json
import sys
import os
import inspect

# Ensure repo root is importable regardless of launch cwd (same pattern as main.py)
_pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "medusa"
SERVER_VERSION = "2.3.0-beta"

# Load the module packs (Modules/Tools, Modules/Mods) so every backend tool
# is dispatchable. Idempotent; must run before building the tool registry.
from medusa.modules.loader import discover_modules, get_module_tools

discover_modules()

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
    "search_kb": "Search the local knowledge base for prior findings.",
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
    return TOOL_DESCRIPTIONS.get(name, f"Medusa backend tool: {name}")


def _py_type_to_json(ann) -> str:
    if ann in (str, int, float, bool, list, tuple, dict):
        return {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", tuple: "array", dict: "object"}[ann]
    return "string"


def _schema_from_signature(func, name: str) -> dict:
    """Build an MCP inputSchema from a Python callable's signature."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        sig = None
    if sig is None or any(
        p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        for p in sig.parameters.values()
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
        prop = {"type": _py_type_to_json(param.annotation) if param.annotation is not inspect.Parameter.empty else "string"}
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


SPECIAL_TOOL_NAMES = {"execute_terminal", "medusa_detect", "medusa_kg_attacker", "medusa_status"}


def _build_backend_tools():
    """One MCP tool per backend tool, with a real name and schema."""
    from medusa.tools.dispatch import list_route_tools

    module_tools = get_module_tools()
    tools = []
    seen = set(SPECIAL_TOOL_NAMES) | {"medusa_tool"}
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
                        "properties": {"args": {"type": "object", "description": "Tool arguments keyed by parameter name"}},
                    },
                }
            )
    return tools


def _make_route_handler(name: str):
    def handler(args):
        from medusa.tools.dispatch import route_tool

        result = route_tool(name, args or {}, {})
        header = (
            f"**[medusa] tool: {name}**\n"
            f"**[medusa] args:** `{json.dumps(args or {}, default=str)}`\n\n"
            f"```text\n{result}\n```"
        )
        return header

    return handler


TOOLS = [
    {
        "name": "medusa_tool",
        "description": "Fallback dispatcher: run any Medusa backend tool by name "
                       "(tool_name + args dict). Prefer the individually exposed tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Medusa tool name"},
                "args": {"type": "object", "description": "Tool arguments keyed by parameter name"},
            },
            "required": ["tool_name"],
        },
    },
    {
        "name": "execute_terminal",
        "description": "Execute a shell command through the Medusa dispatch layer. "
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
        "name": "medusa_detect",
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
        "name": "medusa_kg_attacker",
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
        "name": "medusa_status",
        "description": "Engine status: version, backend health, tool count.",
        "inputSchema": {"type": "object", "properties": {}},
    },
] + _build_backend_tools()


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

def tool_medusa_tool(args):
    tool_name = args.get("tool_name", "")
    tool_args = args.get("args") or {}
    from medusa.tools.dispatch import route_tool
    return route_tool(tool_name, tool_args, {})


def tool_execute_terminal(args):
    from medusa.tools.dispatch import execute_terminal
    return execute_terminal(args.get("cmd", ""), timeout=int(args.get("timeout", 30)))


def tool_medusa_detect(args):
    from medusa.core.blue.tui.feed import _detect_obvious_attack
    result = _detect_obvious_attack(args.get("request") or {})
    return json.dumps(result, indent=2)


def tool_medusa_kg_attacker(args):
    from medusa.core.blue.knowledge_graph import get_kg
    return json.dumps(get_kg().get_attacker_history(args.get("ip", "")), indent=2, default=str)


def tool_medusa_status(args):
    from medusa.tools.dispatch import list_route_tools
    from medusa.core.constants import BLUE_LAB_PORT, PROXY_DEFAULT_PORT

    tools = list_route_tools()
    return json.dumps({
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": PROTOCOL_VERSION,
        "blue_lab_port": BLUE_LAB_PORT,
        "proxy_default_port": PROXY_DEFAULT_PORT,
        "tool_count": len(tools),
        "tools_sample": tools[:10],
    }, indent=2)


TOOL_HANDLERS = {
    "medusa_tool": tool_medusa_tool,
    "execute_terminal": tool_execute_terminal,
    "medusa_detect": tool_medusa_detect,
    "medusa_kg_attacker": tool_medusa_kg_attacker,
    "medusa_status": tool_medusa_status,
}
for _entry in TOOLS:
    _name = _entry["name"]
    if _name not in TOOL_HANDLERS:
        TOOL_HANDLERS[_name] = _make_route_handler(_name)


def handle_message(msg):
    method = msg.get("method")
    request_id = msg.get("id")

    if method == "initialize":
        return _jsonrpc_result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "notifications/initialized":
        return None  # notification — no response

    if method == "ping":
        return _jsonrpc_result(request_id, {})

    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
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
