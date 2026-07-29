# FFUF

> ⚠️ **LONG-RUNNING** — Always use `"background": true`. Fuzzing with large wordlists takes minutes.
> `{"tool_name": "ffuf_fuzz", "tool_args": {"url": "...", "wordlist": "...", "background": true}}` (`ffuf_fuzz`)

Fast web fuzzer. For parameters, dirs, vhosts, headers.

## Installation

- Homebrew: `brew install ffuf`
- APT: `sudo apt install ffuf`
- Go: `go install github.com/ffuf/ffuf/v2@latest`

## Usage

```json
{"tool": "ffuf_fuzz", "args": {"url": "http://TARGET/FUZZ", "wordlist": "/usr/share/wordlists/dirb/common.txt", "options": "-fc 403,404 -t 100"}}
{"tool": "ffuf_fuzz", "args": {"url": "http://TARGET/page?id=FUZZ", "wordlist": "/usr/share/wordlists/sqli.txt", "options": "-fw 100"}}
```

## Key Flags

| Flag | Purpose |
|------|---------|
| `-fc 403,404` | Filter status codes |
| `-fs 1000` | Filter by response size |
| `-fw 100` | Filter by word count |
| `-fr "error"` | Filter by regex |
| `-mc 200,301` | Match status codes |
| `-t 200` | Threads |
| `-X POST` | HTTP method |
| `-d "param=FUZZ"` | POST body |
| `-H "Host: FUZZ"` | Vhost fuzzing |
| `-ac` | Auto-calibrate |
| `-p 0.1` | Request delay |

## Fuzzing Keywords

| URL Pattern | Purpose |
|-------------|---------|
| `http://TARGET/FUZZ` | Directory fuzzing |
| `http://TARGET/page?param=FUZZ` | Parameter fuzzing |
| `http://TARGET/PARAM?user=KEY` | Custom keyword with `-w list.txt:KEY` |
| `-H "Host: FUZZ.TARGET"` | Vhost enumeration |

## Response Signals

| Signal | Meaning |
|--------|---------|
| Different status code | Found |
| Different size | Possible find |
| Same size across all | Auto-calibrate needed (`-ac`) |
