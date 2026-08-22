# MCP Nuclei

> [warn] **LONG-RUNNING** — use `"background": true`

Structured JSON output, auto-deduplicated by template ID. Severity-ranked findings.

```json
{"tool": "mcp_nuclei_scan", "args": {"target": "https://target.com", "templates": "cve,exposures,misconfig", "background": true}}
{"tool": "mcp_nuclei_update", "args": {}}
```