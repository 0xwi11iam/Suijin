#!/usr/bin/env bash
# Medusa TUI launcher.
# - Plain TUI launch: runs with cwd inside packages/opencode (bun resolves
#   tsconfig and node_modules from the process cwd) and passes the repo root
#   as the project positional so medusa.json and .medusa/agents are found.
# - Subcommands (mcp list, agent list, run ...): run from the repo root,
#   where config discovery and relative MCP paths already work.
ROOT="$(cd "$(dirname "$0")" && pwd)"
has_positional=0
for arg in "$@"; do
  case "$arg" in
    -*) ;;
    *) has_positional=1 ;;
  esac
done
if [ "$has_positional" -eq 0 ]; then
  cd "$ROOT/tui/packages/opencode" || exit 1
  exec bun --conditions=browser src/index.ts "$@" "$ROOT"
fi
cd "$ROOT" || exit 1
exec bun --conditions=browser tui/packages/opencode/src/index.ts "$@"
