# Mandatory Note-Taking Protocol

You MUST write notes after significant actions. Each engagement gets its own file — findings never bleed between runs.

## When to Write Notes

Write a note after EVERY one of these events:

| Trigger | Category | Example |
|---|---|---|
| Start of new engagement | `general` | "Starting CloudMart SaaS penetration test" |
| Service/version fingerprint discovered | `recon` | "Apache 2.4.49 detected on port 80" |
| CVE confirmed or disproven | `cve` | "CVE-2021-41773 CONFIRMED — path traversal works" |
| Exploit succeeded or failed | `exploit` | "msf_run eternalblue → shell on session 2" |
| WAF or filter pattern identified | `blocked` | "WAF blocks ' OR 1=1 — confirmed via synonym test" |
| Oracle diagnosis completed | `oracle` | "H1 confirmed: backend syntax error from unescaped quote" |
| Credentials or sensitive data found | `finding` | "Found admin:password123 in /etc/passwd" |
| Every ~3 HTTP requests or tool calls | `progress` | "Tested 3 SQLi payloads on /login — no anomalies yet" |
| Engagement objective met | `complete` | "Flag captured: flag{pwn3d_th3_s3rv3r}" |

## How to Write Notes

**Start of engagement** — create a new notes file by passing the engagement name:

```json
{"tool": "write_note", "args": {
  "content": "Starting CloudMart SaaS penetration test. Target: http://127.0.0.1:5400",
  "success": true,
  "category": "general",
  "engagement": "cloudmart"
}}
```

This creates `.notes/cloudmart_notes.md`. All subsequent notes for this engagement use the same `engagement` value and append to the same file.

**During the engagement:**

```json
{"tool": "write_note", "args": {
  "content": "SQL injection confirmed on /login endpoint.\n\nPayload: admin@cloudmart.io'-- \nResult: HTTP 302 redirect to /dashboard — authentication bypassed.\n\nNext step: Access /manage admin panel with session.",
  "success": true,
  "category": "exploit",
  "engagement": "cloudmart"
}}
```

- `content` — Always include: what you did, the exact payload/command, the result, and what you plan to do next.
- `success` — `true` if the action worked, `false` if it failed or was blocked.
- `category` — One of: `recon`, `exploit`, `cve`, `blocked`, `oracle`, `finding`, `progress`, `complete`, `general`
- `engagement` — Name of the current engagement (e.g. `"cloudmart"`, `"internal-lab"`). Use the same name for all notes on the same target. Creates a new file per engagement.

## Notes File Location

Notes are stored in the `.notes/` directory with per-engagement filenames:

```
.notes/cloudmart_notes.md       # CloudMart engagement
.notes/examplecorp_notes.md     # Another engagement
.notes/2026-06-25_notes.md      # Auto-named if no engagement given
```

Each file contains timestamped entries:

```
# Medusa Engagement Notes — cloudmart
Started: 2026-06-25 10:54:21
---

---
### 2026-06-25 10:54:21 — ✅ SUCCESS
**Category:** recon

CloudMart SaaS Recon Summary...
```

You do NOT need to add timestamps or headers — the tool does this for you.

## The Golden Rule

**If you discover something, WRITE IT DOWN.** Start a new notes file for every engagement with the `engagement` parameter. Findings that aren't recorded are findings that will be forgotten and repeated at cost.
