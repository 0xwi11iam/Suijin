# cURL (`curl_request`)

Manual HTTP requests for targeted payload testing ONLY. For scanning, use gobuster/nmap/ffuf.

## Installation

Built-in on macOS/Linux. Otherwise: `brew install curl` or `sudo apt install curl`.

## Usage

```json
{"tool": "curl_request", "args": {"url": "http://TARGET/login", "method": "POST", "data": "user=admin&pass=test", "headers": "-H 'Cookie: session=abc123'"}}
```

## Key curl Flags

| Flag | Purpose |
|------|---------|
| `-s` | Silent |
| `-i` | Include headers in output |
| `-v` | Verbose |
| `-X POST` | Method |
| `-d "data"` | POST body |
| `-H "X: Y"` | Custom header |
| `-b "cookie=val"` | Send cookie |
| `-c file` | Save cookies |
| `-L` | Follow redirects |
| `-k` | Insecure TLS |
| `-o file` | Output to file |
| `-A "UA"` | User-Agent |
| `-e "URL"` | Referer |
| `-x proxy:port` | Proxy (Burp: `-x http://127.0.0.1:8080`) |
| `-F "file=@/path"` | Upload file |
| `--path-as-is` | Don't normalize path (LFI) |
| `-w "%{http_code}"` | Extract status code |
| `--max-time 10` | Timeout |

## Workflows

```json
{"tool": "curl_request", "args": {"url": "http://TARGET/search?q=test", "headers": "-H 'User-Agent: Mozilla/5.0'"}}
```

For LFI: use `execute_terminal` directly:
```json
{"tool": "execute_terminal", "args": {"cmd": "curl -s --path-as-is \"http://TARGET/page.php?file=../../../../etc/passwd\""}}
```

For Burp debugging:
```json
{"tool": "execute_terminal", "args": {"cmd": "curl -s -x http://127.0.0.1:8080 http://TARGET"}}
```
