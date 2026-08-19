# TruffleHog

> ⚠️ **LONG-RUNNING** — always use `"background": true`

Scans for 700+ types of secrets: AWS keys, GCP SA keys, GitHub tokens, JWT, private keys, database creds, Slack webhooks, etc.

```json
{"tool": "trufflehog_scan", "args": {"target": "https://github.com/org/repo", "flags": "--only-verified", "background": true}}
```