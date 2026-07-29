# Medusa Agent Workspace

This is the AI agent's sandbox directory. The agent has full freedom to:

- Write scripts, payloads, and tools here
- Run commands with `cwd` set to this directory
- Read, write, and delete files within this workspace
- Create subdirectories for organizing work

## Restrictions

- **Installing system packages** (pip, brew, apt, npm -g) requires user approval
- **`sudo` or root-level commands** are blocked and require user approval
- **Writing or deleting files outside this workspace** is permitted but logged
- **Network listeners** (`nc -l`, reverse shells servers) are allowed

## Structure

```
medusa_agent/
├── README.md           ← this file
├── payloads/           ← generated exploit payloads
├── scripts/            ← Python/Bash helper scripts
├── outputs/            ← command output captures
└── notes/              ← any scratch notes
```
