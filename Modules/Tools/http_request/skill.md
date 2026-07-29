# HTTP Request (`http_request`)

Send HTTP requests with browser fingerprinting. For **manual targeted payload testing only** — use `nmap_scan` / `gobuster_dir` / `feroxbuster_scan` / `ffuf_fuzz` for scanning.

## Installation

Built-in (uses `requests` library, already installed). No additional setup needed.

## All Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `method` | string | `GET` | HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS |
| `url` | string | (required) | Full URL including protocol and path |
| `headers` | dict/JSON | `{}` | Custom headers as JSON object. Overrides defaults |
| `body` | string | `""` | Request body (for POST, PUT, PATCH) |

## Default Headers (Browser Emulation)

The tool automatically adds Chrome-like headers:
```
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...
Accept: text/html,application/xhtml+xml,...
Sec-Ch-Ua: "Chromium";v="124"
Upgrade-Insecure-Requests: 1
```

## Workflows

### Basic GET
```json
{"tool": "http_request", "args": {"method": "GET", "url": "http://TARGET/"}}
```

### POST with form data
```json
{"tool": "http_request", "args": {"method": "POST", "url": "http://TARGET/login", "body": "username=admin&password=test"}}
```

### Custom headers (JSON stored as escaped string)
```json
{"tool": "http_request", "args": {"method": "GET", "url": "http://TARGET/admin", "headers": "{\"Authorization\": \"Bearer token123\", \"X-Forwarded-For\": \"127.0.0.1\"}"}}
```

### JSON API call
```json
{"tool": "http_request", "args": {"method": "POST", "url": "http://TARGET/api/v1/users", "headers": "{\"Content-Type\": \"application/json\"}", "body": "{\"username\":\"admin\",\"role\":\"admin\"}"}}
```

## Response Format

```
Status: 200
Headers: {'Content-Type': 'text/html', 'Server': 'Apache/2.4.49'}
Cookies: {'session': 'abc123'}
Body:
<html>...
```

## When NOT to use this

| Use Case | Use Instead |
|----------|-------------|
| Directory brute-forcing | `gobuster_dir` or `feroxbuster_scan` |
| Port scanning | `nmap_scan` |
| Parameter fuzzing | `ffuf_fuzz` |
| SQL injection testing | `sqlmap_scan` |
| Password brute-forcing | `hydra_brute` |
| Vulnerability scanning | `nikto_scan` |
| Subdomain enumeration | `amass_enum` or `gobuster_dns` |
