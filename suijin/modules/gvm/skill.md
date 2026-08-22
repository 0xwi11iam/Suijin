# GVM/OpenVAS

> [warn] **EXTREMELY LONG-RUNNING** (10-60 min) — always use `"background": true`

170,000+ Network Vulnerability Tests via Greenbone Community Feed. Requires GVM installed and running.

```json
{"tool": "gvm_scan", "args": {"target": "10.0.0.1", "config": "Full and fast", "background": true}}
{"tool": "gvm_list_tasks", "args": {}}
{"tool": "gvm_get_results", "args": {"task_id": "uuid-here"}}
```