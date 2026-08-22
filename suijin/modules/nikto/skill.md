# Nikto

> [warn] **LONG-RUNNING** — Always use `"background": true`. Full web server scan takes 2-10 minutes.
> `{"tool_name": "nikto_scan", "tool_args": {"url": "...", "background": true}}` (`nikto_scan`)

Web server vulnerability scanner. Finds outdated software, misconfigurations, default files.

## Installation

- Homebrew: `brew install nikto`
- APT: `sudo apt install nikto`
- Git: `git clone https://github.com/sullo/nikto.git /opt/nikto`

## Usage

```json
{"tool": "nikto_scan", "args": {"url": "http://TARGET:80"}}
```

## Key Flags

| Flag | Purpose |
|------|---------|
| `-h URL` | Target |
| `-p 80,443,8080` | Ports |
| `-ssl` | Force SSL |
| `-no404` | Disable 404 detection (faster) |
| `-t 5` | Tuning (1-9 for different vuln types) |
| `-Format html` | Output format |
| `-o report.html` | Output file |
| `-evasion 1` | Encoding evasion |
| `-list-plugins` | List all plugins |

## Notable Findings (by severity)

| Finding | Risk |
|---------|------|
| "/cgi-bin/test-cgi" | CGI enabled — RCE |
| "Apache/2.4.49" | Path traversal CVE-2021-41773 |
| "/wp-admin/" | WordPress |
| "PHP version X.X.X" | Old PHP |
| "Missing X-Frame-Options" | Clickjacking |
| "Server leaks inodes" | Info disclosure |

## Workflows

```json
{"tool": "nikto_scan", "args": {"url": "http://TARGET:80"}}
```
```json
{"tool": "execute_terminal", "args": {"cmd": "nikto -h https://TARGET -ssl -no404 -o /tmp/nikto.html -F html"}}
```
