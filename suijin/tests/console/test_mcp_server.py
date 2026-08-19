"""Tests for suijin/mcp_server.py — the MCP sidecar bridging TUI to backend."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER = REPO_ROOT / "suijin" / "modules" / "console" / "lib" / "mcp_server.py"


def _call_server(lines):
    """Run the MCP server with scripted JSON-RPC input, return responses."""
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(json.dumps(msg) for msg in lines) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    responses = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


class TestMCPProtocol:
    def test_initialize(self):
        responses = _call_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            ]
        )
        assert responses[0]["id"] == 1
        result = responses[0]["result"]
        assert result["serverInfo"]["name"] == "suijin"
        assert "tools" in result["capabilities"]

    def test_tools_list(self):
        responses = _call_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            ]
        )
        tools = responses[0]["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"suijin_tool", "execute_terminal", "suijin_detect", "suijin_kg_attacker", "suijin_status"} <= names

    def test_per_tool_registry(self):
        """Every backend tool is exposed under its own name with a schema."""
        responses = _call_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            ]
        )
        tools = {t["name"]: t for t in responses[0]["result"]["tools"]}
        # Module packs must be discovered: these are the core recon tools.
        assert {"nmap_scan", "gobuster_dir", "sqlmap_scan", "write_note", "claim_flag", "search_cve"} <= set(tools)
        assert len(tools) >= 100
        # Real signature-derived schema: target required, flags has a default.
        schema = tools["nmap_scan"]["inputSchema"]
        assert schema["required"] == ["target"]
        assert schema["properties"]["target"]["type"] == "string"
        assert schema["properties"]["flags"]["default"] == "-sV -sC"

    def test_named_tool_call(self):
        """Calling a backend tool directly by name routes and reports the call."""
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "claim_flag", "arguments": {"flag": "FLAG{direct}"}},
                },
            ]
        )
        text = responses[0]["result"]["content"][0]["text"]
        assert "FLAG{direct}" in text
        assert "[suijin] tool: claim_flag" in text

    def test_status_reports_tool_count(self):
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "suijin_status", "arguments": {}},
                },
            ]
        )
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["tool_count"] >= 100

    def test_notification_no_response(self):
        responses = _call_server(
            [
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            ]
        )
        assert len(responses) == 1  # notification gets no response
        assert responses[0]["id"] == 1

    def test_unknown_method_error(self):
        responses = _call_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "bogus/method", "params": {}},
            ]
        )
        assert responses[0]["error"]["code"] == -32601


class TestMCPTools:
    def test_suijin_status(self):
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "suijin_status", "arguments": {}},
                },
            ]
        )
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["server"] == "suijin"
        assert data["version"]

    def test_suijin_detect_sqli(self):
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "suijin_detect",
                        "arguments": {
                            "request": {
                                "method": "POST",
                                "path": "/auth/login",
                                "body": "admin' OR '1'='1",
                                "ip": "127.0.0.1",
                            }
                        },
                    },
                },
            ]
        )
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["score"] >= 5
        assert any("SQL" in p[0] for p in data["patterns"])

    def test_suijin_detect_clean(self):
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "suijin_detect",
                        "arguments": {"request": {"method": "GET", "path": "/health", "body": "", "ip": "10.0.0.1"}},
                    },
                },
            ]
        )
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["score"] < 5

    def test_execute_terminal(self):
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "execute_terminal", "arguments": {"cmd": "echo mcp_test_ok"}},
                },
            ]
        )
        text = responses[0]["result"]["content"][0]["text"]
        assert "mcp_test_ok" in text

    def test_execute_terminal_blocked(self):
        """Guardrails must block destructive commands through MCP too."""
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "execute_terminal", "arguments": {"cmd": "rm -rf /"}},
                },
            ]
        )
        text = responses[0]["result"]["content"][0]["text"]
        assert "denied" in text.lower()

    def test_kg_attacker_empty(self):
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "suijin_kg_attacker", "arguments": {"ip": "1.2.3.4"}},
                },
            ]
        )
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["total_flags"] == 0

    def test_unknown_tool_error(self):
        responses = _call_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "not_a_tool", "arguments": {}}},
            ]
        )
        result = responses[0]["result"]
        assert result["isError"] is True
        assert "Unknown tool" in result["content"][0]["text"]

    def test_suijin_tool_routes(self):
        """Generic dispatch through route_tool (claim_flag is pure)."""
        responses = _call_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "suijin_tool",
                        "arguments": {"tool_name": "claim_flag", "args": {"flag": "FLAG{mcp_test}"}},
                    },
                },
            ]
        )
        text = responses[0]["result"]["content"][0]["text"]
        assert "FLAG{mcp_test}" in text
