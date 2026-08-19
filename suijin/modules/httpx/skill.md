# HTTPX

> ⚠️ **LONG-RUNNING** with many URLs — use `"background": true`

HTTP probing and fingerprinting. Returns status code, title, tech stack, server, content-length, redirect location.

```json
{"tool": "httpx_probe", "args": {"url": "https://target.com", "flags": "-status-code -title -tech-detect -server"}}
```