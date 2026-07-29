# Gobuster

> ⚠️ **LONG-RUNNING** — Always use `"background": true`. Directory brute-force with large wordlists takes minutes.
> `{"tool_name": "gobuster_dir", "tool_args": {"url": "...", "wordlist": "...", "background": true}}` (`gobuster_dir`, `gobuster_dns`)

Directory/file brute-forcing for web servers and DNS subdomain enumeration.

## Installation

| Method | Command |
|--------|---------|
| **Homebrew (macOS)** | `brew install gobuster` |
| **APT (Kali)** | `sudo apt install gobuster` |
| **Go install** | `go install github.com/OJ/gobuster/v3@latest` (then `cp ~/go/bin/gobuster /usr/local/bin/`) |
| **Snap** | `sudo snap install gobuster` |
| **Git + build** | `git clone https://github.com/OJ/gobuster.git && cd gobuster && go build -o gobuster main.go` |

## Parameters

### gobuster_dir
| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | Target URL (e.g. `http://TARGET`) |
| `wordlist` | Yes | Path to wordlist file |
| `extensions` | No | Comma-separated extensions: `php,html,txt,asp` |
| `threads` | No | Concurrent threads (default: 20) |

### gobuster_dns
| Parameter | Required | Description |
|-----------|----------|-------------|
| `domain` | Yes | Target domain (e.g. `example.com`) |
| `wordlist` | Yes | Subdomain wordlist |

## Essential Flags

### Directory Mode Flags
| Flag | Description |
|------|-------------|
| `-t 50` | Threads (default 10; more = faster but louder) |
| `-x php,html,txt` | File extensions to check |
| `-s 200,204,301,302` | Status codes to show (default: 200,204,301,302,307,401,403) |
| `-b 403,404` | Status codes to hide |
| `-k` | Skip TLS verification |
| `-r` | Follow redirects |
| `-n` | No recursion |
| `-q` | Quiet mode |
| `-U username` | Basic auth username |
| `-P password` | Basic auth password |
| `-H "Cookie: x=y"` | Custom header |
| `-w wordlist.txt` | Wordlist path |
| `-u http://TARGET` | URL target |
| `-o output.txt` | Output file |
| `-f` | Append `/` to directories |

### DNS Mode Flags
| Flag | Description |
|------|-------------|
| `-d example.com` | Target domain |
| `-r 8.8.8.8` | Custom DNS resolver |
| `-c` | Show CNAME records |
| `-i` | Show IP addresses |
| `-t 50` | Concurrent DNS requests |

## Wordlists

| Location | Contains |
|----------|----------|
| `/usr/share/wordlists/dirb/common.txt` | 4600 common web paths |
| `/usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt` | 220k paths |
| `/usr/share/wordlists/seclists/Discovery/Web-Content/` | Various web content lists |
| `/usr/share/wordlists/seclists/Discovery/DNS/subdomains-top1million-5000.txt` | Top 5000 subdomains |
| `/usr/share/amass/wordlists/subdomains.lst` | Amass subdomain list |

## Workflows

### Standard directory brute-force
```json
{"tool": "gobuster_dir", "args": {"url": "http://TARGET", "wordlist": "/usr/share/wordlists/dirb/common.txt", "extensions": "php,html,txt", "threads": 30}}
```

### With authentication
```json
{"tool": "execute_terminal", "args": {"cmd": "gobuster dir -u http://TARGET -w /usr/share/wordlists/dirb/common.txt -x php -t 40 -H 'Cookie: session=abc123' --no-color"}}
```

### Subdomain enumeration
```json
{"tool": "gobuster_dns", "args": {"domain": "example.com", "wordlist": "/usr/share/wordlists/seclists/Discovery/DNS/subdomains-top1million-5000.txt"}}
```

### CMS-specific enumeration (WordPress)
```json
{"tool": "execute_terminal", "args": {"cmd": "gobuster dir -u http://TARGET/wp-content -w /usr/share/wordlists/dirb/common.txt -x php,html -t 30"}}
```

## Response Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK — found! |
| 301/302 | Redirect — may indicate valid path |
| 401 | Unauthorized — exists but requires auth |
| 403 | Forbidden — exists but access denied |
| 404 | Not found — path doesn't exist |
| 500 | Server error — may indicate vulnerable path |
