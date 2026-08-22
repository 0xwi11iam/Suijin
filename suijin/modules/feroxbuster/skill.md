# Feroxbuster

> [warn] **LONG-RUNNING** — Always use `"background": true`. Recursive content discovery takes minutes.
> `{"tool_name": "feroxbuster_scan", "tool_args": {"url": "...", "wordlist": "...", "background": true}}` (`feroxbuster_scan`)

Recursive directory brute-forcing (Rust). Better than gobuster for deep sites.

## Installation

- Homebrew: `brew install feroxbuster`
- APT: `sudo apt install feroxbuster`
- Cargo: `cargo install feroxbuster`

## Usage

```json
{"tool": "feroxbuster_scan", "args": {"url": "http://TARGET", "wordlist": "/usr/share/wordlists/dirb/common.txt", "extensions": "php,html,txt", "threads": 40}}
```

## Key Flags

| Flag | Purpose |
|------|---------|
| `-x php,html` | Extensions |
| `-t 50` | Threads |
| `-s 200,301` | Show only these status codes |
| `--filter-status 404` | Hide 404s |
| `--filter-size X` | Filter by body size |
| `-H "Cookie: x=y"` | Custom header |
| `-r` | Recursion (default on) |
| `-d 3` | Max depth |

## vs Gobuster

| Feature | Feroxbuster | Gobuster |
|---------|-------------|----------|
| Recursion | Auto | Manual |
| Speed | Faster (Rust) | Fast (Go) |
| Filter by size/regex | Yes | No |
| Extensions per dir | Auto-detect | Static list |
