# MCP Nmap

> ⚠️ **LONG-RUNNING** — use `"background": true`

Structured nmap with XML output parsed into JSON. Returns IP, ports, services, versions.

```json
{"tool": "mcp_nmap_scan", "args": {"target": "10.0.0.1", "flags": "-sV -sC -p- -T4", "background": true}}
```