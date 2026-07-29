# Core Utils (`search_kb`, `apply_patch`, `claim_flag`)

Built-in utilities.

## search_kb

Search local knowledge base:
```json
{"tool": "search_kb", "args": {"keyword": "SQL injection"}}
```

## apply_patch

Patch vulnerability in lab app (Blue Team):
```json
{"tool": "apply_patch", "args": {"vulnerability": "sqli"}}
```
Options: `sqli`, `cmdi`, `ssrf`, `ssti`, `xss`, `idor_xss`

## claim_flag

Signal objective complete:
```json
{"tool": "claim_flag", "args": {"flag": "flag{...}"}}
```
