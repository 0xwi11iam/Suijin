# Credential Store

Persist discovered credentials during engagements. Auto-loaded on next run.

```json
{"tool": "creds_add", "args": {"service": "aws", "cred_type": "api_key", "value": "AKIA...", "username": "", "notes": "Found at /admin/creds on port 5401"}}
{"tool": "creds_list", "args": {"filter": "aws"}}
{"tool": "creds_get", "args": {"service": "admin_panel"}}
```

Stored at `suijin_agent/credentials.json`. Entries are deduplicated by service+value.