# Suijin — Developer Guide

This is a large, deliberately structured codebase. This document is the
map: where things live, why, and how to change them safely.

## The one-paragraph mental model

Suijin is an operating system for security automation. A tiny **kernel**
(stdlib-only, ~1,400 lines) boots **modules** — self-contained units that
register tools and services on a shared **Context** — and the agent,
consoles, and 84 tool packs are all just modules. No module imports
another module: every coupling goes through the kernel (manifest
`requires` + Context services). Tests enforce this structurally, so the
architecture cannot rot by accident.

## Repository tour

```
suijin/
├── kernel/          the OS core — DO NOT import app code here (tested)
│   ├── controller.py   boot(): scan roots -> resolve DAG -> lifecycle
│   ├── context.py      the Context: services, tools, call_tool audit
│   ├── registry.py     manifest parsing, unit table, collisions
│   ├── audit.py        append-only JSONL tool audit (all surfaces)
│   ├── native.py       resolve_dag + check_paths (pure implementation)
│   ├── config.py       LayeredConfig (deep-merge, immutable layers)
│   ├── journal.py      ring-buffer journal, atomic flush
│   ├── vfs.py          workspace path containment
│   └── ...             events, jobs, health, security, errors
├── modules/         ALL application code — 12 first-party + 84 packs
│   ├── platform/    workspace, runtime, config, helpers, security, infra
│   ├── tools/       dispatch router + recon/intel/http/reporting lib
│   ├── agent/       nodes, prompts, compiled skills, the graph brain
│   ├── providers/   LLM layer (generate, failover, usage)
│   ├── knowledge/   offline KB + KEV mirror
│   ├── ops/         engagement lifecycle (export/debrief/replay/...)
│   ├── blueteam/    defense stack (proxy, deception, SOC, traffic)
│   ├── redteam/     offense stack + intel knowledge graph
│   ├── console/     CLI router, TUIs, MCP server
│   ├── skills/      drop-in markdown skills loader
│   ├── addons/      zero-boilerplate tool drops loader
│   └── <packs>/     84 tool packs (manifest.json = the marker)
├── skills/          drop your .md skills here — they boot into the prompt
├── addons/          drop <name>/main.py here — functions become tools
├── lab/             vulnerable target apps (battle mode spars here)
├── tests/           per-module suites + architecture/ + kernel/
└── main.py          TUI entrypoint (Docker ENTRYPOINT)
suijin_agent/        THE workspace: outputs/ (all artifacts), caches/,
                     configs, scripts/ — the one volume that matters
```

## Module anatomy

Every module home is the same shape:

```
modules/<name>/
├── plugin.json    id, tier (core/recommended/installed), requires,
│                  provides, entry, permissions, description
├── __init__.py    the Module class (register/start/stop)
└── lib/           the implementation (module-internal code)
```

The boundary rule (enforced by `tests/kernel/test_phase5_boundary.py`):
inside `modules/**`, module-level imports may be stdlib, kernel, or
your OWN module — anything else must be function-local. Cross-module
work happens through `ctx.service(...)` and `ctx.call_tool(...)`.

## The extension ladder

Four rungs, zero overlap — pick the smallest one that fits:

| Rung | You write | You get | Where |
|---|---|---|---|
| **Skill** | `foo.md` | prompt knowledge | `suijin/skills/` |
| **Addon** | `foo/main.py` (plain functions) | auto-registered tools | `suijin/addons/` |
| **Pack** | manifest + main + skill.md | tools + skills + kernel unit | `suijin/modules/` or `~/.suijin/modules/` |
| **Module** | plugin.json + `__init__.py` + lib/ | full lifecycle + services | `suijin/modules/` |

### Add a skill (30 seconds)
Drop `suijin/skills/my-skill.md`. Next boot it's in the system prompt
(8KB/file, 64KB total budget). `<!-- skip` keeps a draft dormant.

### Add an addon (2 minutes)
`suijin/addons/mytools/main.py`:
```python
def zap(host: str = "") -> str:
    """Zap a host."""
    if not host:
        return "Error: host required"
    return f"zapped {host}"
```
Public callables only; return strings; validate inputs. They become
tools at boot (dispatch + kernel + catalog — tested). Graduating:
`suijin module adopt mytools`.

### Add a pack (5 minutes)
`suijin module init mypack` scaffolds `~/.suijin/modules/mypack/`
(manifest.json + main.py + skill.md + plugin.json + entry.py — it boots).
Implement the functions, `suijin module validate mypack`.

### Add a first-party module
Copy `modules/skills/` (smallest real example) or `modules/addons/`.
Write plugin.json + the Module class. Respect the boundary rule.

## Where common work happens

- **A new CLI verb**: `modules/console/lib/cli.py` (router) — handlers
  live with their owning module when heavy (see ops verbs for the pattern).
- **A new tool for the agent**: pack or addon (above). Tools must appear
  in the catalog — `tests/tools/test_dispatch.py::TestCatalogParity`
  fails the build if the model can't see a routed tool.
- **A new LLM provider**: `modules/providers/lib/__init__.py`.
- **A new KB source**: `modules/knowledge/lib/kb.py` (SOURCES).
- **Workspace paths**: NEVER hardcode — use `platform.lib.workspace`
  (`artifact_dir(name)` for artifacts; artifacts live under `outputs/`).

## The audit trail

Every tool invocation on every surface is recorded, append-only:
`outputs/audit_trails/{tool_calls,agent_steps,cli_calls}.jsonl`. Arg
values are never stored (key names + sha256 digest). Extend surfaces by
calling `ToolAudit.record(...)` — never raise from audit code.

## Deploy paths (four, all tested)

| Platform | Script | Notes |
|---|---|---|
| macOS / Linux native | `install.sh` | interactive start (OS + pip), full dependency auto-resolution |
| Any Docker host | `docker.sh` + `.env.example` | one command; colima or Desktop |
| Windows | `install.ps1` | Docker-only by policy (native refused with a pointer) |
| Existing Kali container | `kali-setup.sh` | curl-pipe; hard-stops on non-Kali |

The Docker image is minimal-footprint by design (kali-linux-core +
curated tools; metasploit behind `--build-arg WITH_METASPLOIT=1`).

## Notable subsystems

- **Self-critique** (`modules/agent/lib/critique.py`): post-run LLM
  review; report to `outputs/reports/critique_*.md`, tactics to the KG.
- **Sparring** (`modules/ops/lib/sparring.py`): detector practice with
  baselines under `outputs/spar_baselines/`; `--fail-on-regression`
  gives CI semantics.
- **Audit trail** (`kernel/audit.py`): every tool call on every
  surface, append-only, arg digests only.
- **Cost governor** (`platform/lib/governor.py`): hard budget stop in
  the think loop (`max_cost_usd`); metering (`ops/lib/metering.py`)
  powers the `suijin status` leaderboard/forecast.
- **Recipes** (`tools/lib/recipes.py`): multi-tool macros + the miner
  (`suijin recipes mine`); decomposer (`agent/lib/decompose.py`)
  behind `suijin plan`.
- **Pack authoring gate**: `suijin module test <name>` — run it before
  publishing any pack.

## Test gates (run before every commit)

```bash
.venv/bin/python -m pytest suijin/tests -q -m "not ai and not slow"
.venv/bin/ruff check suijin/ && .venv/bin/ruff format --check suijin/
```

The standing boot probe (run too):
```bash
.venv/bin/python -c "from pathlib import Path; from suijin.kernel import controller; \
ctx, r = controller.boot(module_roots=[Path('suijin/modules')], quiet=True); \
print(len(r.boot_order), 'units', len(ctx.tool_names()), 'tools', len(r.skipped), 'skipped'); ctx.shutdown()"
```

Gate suites that will catch architecture drift:
- `tests/kernel/` — purity (kernel imports only stdlib), boundary,
  contracts, controller faults, audit regressions, hardening
- `tests/architecture/` — import graph (no dangling/old paths),
  packaging, install e2e, audit trail, purity linters
- `tests/tools/test_dispatch.py::TestCatalogParity` — every routed tool
  is advertised to the model

## Conventions that keep this sane

- **Monkeypatch seams**: module attrs are the patch surface. Derived
  constants use lazy accessors (`_const()` / `artifact_dir()`) that
  honor `setattr` on the module — follow that pattern for anything a
  test would want to redirect.
- **Lazy cross-module imports**: function-local `from x import y` at the
  use site. Never at module level in `modules/**`.
- **Patch-transparent delegation**: if you re-export names, use
  `__getattr__` delegation (see any retired shim in git history for the
  pattern) — star-imports snapshot and are patch-blind.
- **Never break the CLI on optional features**: wrap risky imports in
  try/except and degrade loudly.

## Release checklist

1. Full gates green (tests, ruff, boot probe, wheel build).
2. `suijin/version.json` + `pyproject.toml` versions bumped together
   (`tests/architecture/test_packaging.py` pins equality).
3. CHANGELOG entry; README counts updated; ARCHITECTURE.md if structure
   changed; this file if conventions changed.
4. `pip wheel . ` and inspect contents (module libs, skills/, addons/
   README, packs all ship).
