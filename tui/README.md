# Medusa — Terminal UI

The terminal UI for **Medusa**, a dual-mode autonomous security platform.
Green-themed, session-first, and driven by the Medusa Python backend over MCP.

> **Fork attribution**: this directory is a fork of
> [opencode](https://github.com/anomalyco/opencode) (MIT, Copyright (c) 2025 opencode).
> The terminal UI, sessioning engine, provider system, and plugin architecture
> are opencode's work, rebranded and adapted for Medusa. See [LICENSE](./LICENSE)
> for the retained copyright notice.

## What this is

`tui/` is the front-end shell of Medusa. It keeps what made opencode excellent:

- **Sessioning** — durable SQLite-backed conversations with compaction,
  summaries, and resume
- **Providers** — 30+ model providers with per-provider configuration
- **Zen free tier** — zero-cost model access via the Zen gateway
- **Plugin system** — extensible agents, tools, and MCP servers

And changes what Medusa needs:

- **Green identity** — default theme, MED / USA block logo, `medusa.json` config
- **`medusa-red` / `medusa-blue` agents** — switch offensive/defensive modes
- **MCP sidecar** — every Medusa backend tool reachable from the TUI

## Architecture

```
Medusa TUI (this directory, TypeScript)
  ├── packages/tui          terminal UI (SolidJS + OpenTUI)
  ├── packages/opencode     agent runtime, sessions, providers, CLI
  ├── packages/core         domain layer, config, DB, plugins
  └── packages/{ui,llm,schema,protocol,plugin,...}

Medusa backend (../medusa, Python)
  └── medusa/mcp_server.py  stdio MCP bridge — 85 tools, blue SOC, KG
```

The TUI never talks to security tools directly. Every action flows through
`medusa_tool` / `execute_terminal` MCP calls into the Python backend, which
keeps all guardrails, knowledge graph, and engagement state.

## Development

```bash
# from the repo root (parent of tui/):
./medusa-tui.sh          # launch the TUI
./medusa-tui.sh mcp list # inspect the MCP bridge

# inside tui/:
bun install              # install workspaces
bun run typecheck        # tsgo across all packages
bun run dev              # launch the TUI (dev-style)
```

Agents live in `.medusa/agents/*.md` (repo root). MCP servers are registered
in `medusa.json`.

## Configuration

| Item | Location |
|---|---|
| Project config | `medusa.json` / `medusa.jsonc` (walks up to worktree root) |
| Global config | `~/.config/medusa/` |
| Agents | `.medusa/agent(s)/*.md` |
| Modes | `.medusa/mode(s)/*.md` |
| Env prefix | `MEDUSA_*` |

## License

MIT. Original copyright retained — see [LICENSE](./LICENSE).
Built on [opencode](https://github.com/anomalyco/opencode) by the opencode team;
this fork is not affiliated with or endorsed by them.

