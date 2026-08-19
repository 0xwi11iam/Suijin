# Note Taking (`write_note`)

**MANDATORY** — log every significant action. Findings that aren't recorded are wasted.

## Installation

Built-in. No installation needed. Notes go to `.notes/{engagement}_notes.md`.

## All Parameters

| Parameter | Required | Options | Description |
|-----------|----------|---------|-------------|
| `content` | Yes | free text | What happened, what you tried, what the result was |
| `success` | Yes | `true` / `false` | Whether the action succeeded |
| `category` | Yes | see below | Type of finding |
| `engagement` | Yes | string | Target name — creates `.notes/{engagement}_notes.md` |

## Categories

| Category | When to use | Example |
|----------|-------------|---------|
| `general` | Engagement start, config changes | "Starting pentest on 10.0.0.1" |
| `recon` | Fingerprints, scans, discoveries | "Apache 2.4.49 on port 80" |
| `exploit` | Exploit attempts (success/fail) | "SQLi bypassed login via admin'--" |
| `cve` | CVE confirmed or disproven | "CVE-2021-41773 confirmed on Apache 2.4.49" |
| `blocked` | WAF, rate limits, filter patterns | "WAF blocks ' OR 1=1, synonym worked" |
| `oracle` | Oracle diagnosis results | "H1 confirmed: backend syntax error" |
| `finding` | Credentials, secrets, data found | "Found admin:password123" |
| `progress` | Periodic status updates | "Tested 3 SQLi payloads, no anomalies" |
| `complete` | Objective achieved | "Flag captured: flag{...}" |

## Workflows

### Start a new engagement
```json
{"tool": "write_note", "args": {"content": "Starting penetration test. Target: http://10.0.0.1\nTools: nmap, gobuster, sqlmap", "success": true, "category": "general", "engagement": "client-name"}}
```

### Log a recon finding
```json
{"tool": "write_note", "args": {"content": "nmap scan of 10.0.0.1:\n- Port 80: Apache 2.4.49\n- Port 22: OpenSSH 8.2\n- Port 3306: MySQL 5.7\n\nNext: search CVEs for Apache 2.4.49", "success": true, "category": "recon", "engagement": "client-name"}}
```

### Log a failed exploit
```json
{"tool": "write_note", "args": {"content": "SQL injection on /login failed:\nPayload: admin' OR 1=1 --\nResult: HTTP 500 - Internal Server Error\n\nNext: troubleshoot quoting syntax", "success": false, "category": "exploit", "engagement": "client-name"}}
```

### Log completion
```json
{"tool": "write_note", "args": {"content": "OBJECTIVE COMPLETE\nFull chain: nmap → search_cve → msf_run → shell → cat /flag.txt\nFlag: flag{pwn3d}", "success": true, "category": "complete", "engagement": "client-name"}}
```

## Notes File Format

```
# Suijin Engagement Notes — client-name
Started: 2026-06-25 10:54:21
---

---
### 2026-06-25 10:54:21 — ✅ SUCCESS
**Category:** recon

nmap scan of 10.0.0.1...
```
