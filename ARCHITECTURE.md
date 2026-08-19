# Suijin OS — Architecture

> The manual. How the operating system fits together, how to write a
> module, and the rules that keep it modular.

Suijin is an operating system for security automation. A **kernel**
(the low-level system) composes **modules** (the software) at boot: a
core tier that must exist, a recommended tier that ships bundled, and
an installed tier for community software. Everything snaps in and out
like lego — disabling a module removes its tools, services, and menu
entries completely; installing one adds them. The same product, same
commands, same look throughout.

```
                 ┌─────────────────────────────────────────┐
                 │  console (TUI · CLI · WebUI · MCP)      │  ← surfaces, feature-blind
                 ├─────────────────────────────────────────┤
   recommended ──┤  redteam · blueteam · knowledge · ops    │
   + 49 packs    │  providers · nmap · sqlmap · ...        │  ← disableable, bundled
                 ├─────────────────────────────────────────┤
   core          │  platform · tools · agent (graph/       │  ← boot-required
                 │              nodes/ memory)             │
                 ├─────────────────────────────────────────┤
                 │  KERNEL  (12 subsystems, stdlib-only)   │
                 │  contracts · context · events · registry│
                 │  controller · jobs · vfs · security     │
                 │  config · health · journal · errors     │
                 │  ────────────────────────────────────   │
                 │  suijin-core (Rust): resolve_dag,        │  ← compiled heart,
                 │  check_paths + pure-Python oracles       │    optional wheel
                 └─────────────────────────────────────────┘
```

## The kernel

`suijin/kernel/` — ~3,000 lines, **imports nothing outside stdlib and
itself** (enforced two ways: AST scan + clean-interpreter import, in
`test_kernel_purity.py`). It understands *categories* of software,
never specific modules — that's what lets arbitrary future code snap in
without kernel changes.

| Subsystem | OS analogy | Job |
|:----------|:-----------|:----|
| `contracts` | driver model | `Module` / `Tool` protocols, `Tier` enum — the shapes everything implements |
| `context` | syscall table | THE object handed to every module: config, workspace, events, tools registry, lazy services |
| `events` | IPC | pub/sub with per-subscriber fault isolation + re-entrancy depth bound |
| `registry` | init parsing | manifest scan (nested = dotted ids), dependency DAG via the Rust core, collision policy, quarantine |
| `controller` | init system | `boot()` — the composition root; register() all, start() topological, shutdown() reverse |
| `jobs` | scheduler | spawn/status/wait/cancel, capped |
| `vfs` | VFS | the single file chokepoint — workspace-anchored, symlink-normalized, allowlist |
| `security` | security subsystem | permission vocabulary, declared in manifests, enforced at one point |
| `config` | /etc | layered shadowing (kernel→module→user→env), **deep** merge |
| `health` | watchdog | per-module last-boot status → boot report, doctor, Module Manager |
| `journal` | dmesg | ring + rotated disk log; atomic drain; drops are counted, never silent |
| `errors` | — | BootError / DependencyError / PermissionDenied / QuarantinedModule |
| `_pure` + `native` | — | pure-Python oracles + the ONLY file that may touch the compiled core |

### The Rust core

`native/suijin-core/` (PyO3, abi3 wheel): exactly **two** functions
cross the boundary, both JSON-in/JSON-out — `resolve_dag` and
`check_paths`. Zero Python object graph crosses. The pure-Python
implementations are permanent and ship everywhere: they're the fallback
when the wheel is absent AND the test oracle (CI asserts both produce
canonically identical output across fixtures + 600-case fuzz — that
suite caught a real divergence and a real algorithm bug on its first
run). `pipx install suijin` never needs a Rust toolchain.

## Boot — the scene analysis

`controller.boot(module_roots, workspace, enabled_check)`:

1. **SCAN** every root (vendored `suijin/modules/` → `~/.suijin/modules/` → dev
   trees; later sources win). Nested manifests become dotted-id units
   (`agent/graph` → `agent.graph`) — first-class DAG members.
2. **RESOLVE** the DAG (Rust core): boot order, cycles *named*,
   missing deps skip, tier collisions (later tier loses unless the
   manifest declares `overrides`), broken manifests quarantined.
3. **POLICY**: disabled units (operator state) drop before
   materialization — a disabled recommended module's tools/services/
   menus never exist that boot. Disabled *core* aborts with a readable
   reason. Core problems abort; everything else degrades gracefully.
4. **LIFECYCLE**: `register(ctx)` for every module (cheap, no I/O),
   `start(ctx)` in topological order. A failing recommended module
   skips + reports; boot continues.
5. **REPORT**: quiet when healthy; a human summary exactly when
   something was skipped/quarantined/collided. Always in the journal
   (`workspace/logs/journal.log`) and `[b]` in the Module Manager.

Shutdown stops modules in reverse order, best-effort, and flushes the
journal.

## Writing a module — the lego brick

One folder, one manifest, one entry, one test:

```
my-module/
├── plugin.json
└── __init__.py
```

```json
{
  "id": "my-module",            // snake_case; nested: parent.child
  "version": "1.0.0",
  "tier": "installed",          // core|recommended|installed (community = installed)
  "requires": ["platform"],     // module ids; missing deps skip you, never crash boot
  "provides": ["my.service"],
  "entry": "my_module:MyModule",// pkg:Class — materialized for you
  "permissions": ["network"],   // network|shell|filesystem|provider|events.listen|events.emit
  "description": "what it does"
}
```

```python
from suijin.kernel.contracts import Module, Tier

class MyModule(Module):
    id = "my-module"
    tier = Tier.INSTALLED

    def register(self, ctx):          # cheap: declare, don't do
        ctx.register_service("my.service", lambda: make_it())
        ctx.on_event("tool.after", self._watch)

    def start(self, ctx):             # bring live (I/O allowed)
        ctx.register_tool("my.scan", self._scan, owner=self.id)
        hooks = ctx.service("console_hooks")          # optional
        if hooks:
            hooks.register_menu("my", label="My Tool", owner=self.id)
            hooks.register_verb("my", self._run, owner=self.id)

    def stop(self, ctx):              # unregister (make disable = disappear)
        hooks = ctx.service("console_hooks")
        if hooks:
            hooks.unregister_owner(self.id)

    def _scan(self, args, ctx): return "result"
```

**The one rule** (AST-enforced, `test_phase5_boundary.py`): inside
`suijin/modules/`, module-level imports may be stdlib or
`suijin.kernel.*` — everything else goes inside functions (resolved at
boot through the Context). A module that imports another subsystem at
import time is welded, not snapped.

Install: `suijin module install ./my-module` (validates, refuses
core-tier imposters, reports python deps with exact pip commands;
`--with-deps` opts in). Scaffold: `suijin module init`.

## Tiers

| Tier | Ships | Disable | Examples |
|:-----|:-------|:---------|:---------|
| core | wheel | refused (boot aborts, readable reason) | platform, tools, agent, console |
| recommended | wheel | yes — vanishes from every surface | redteam, blueteam, knowledge, ops, providers, 49 packs |
| installed | `~/.suijin/modules/` | yes / uninstall | community modules |

## Enforcement (what keeps it an OS)

- **kernel purity** — AST + clean-interpreter, path-guarded (a vacuous
  scan once hid two violations for days; the guard now fails on empty)
- **module boundaries** — the one-rule AST linter above
- **oracle equality** — Rust and pure implementations, canonically
  identical, fuzzed every CI run
- **regression pins** — every audit bug has a named test
  (`test_audit_regressions.py`): journal atomic drain, event depth
  bound, deep merge, VFS root canonicalization, ...

## Layout (v4.1 — everything is a module)

```
suijin/
├── kernel/          # the OS core (stdlib-pure, frozen)
├── modules/         # ALL code lives here
│   ├── platform/    #   workspace, runtime, config, security, infra
│   ├── tools/       #   recon/intel/http/dispatch/reporting lib
│   ├── agent/       #   nodes, prompts, skills, the graph brain
│   ├── providers/   #   LLM layer
│   ├── knowledge/   #   offline KB + KEV
│   ├── ops/         #   engagement lifecycle verbs
│   ├── blueteam/    #   defense stack
│   ├── redteam/     #   offense stack + intel/KG
│   ├── console/     #   CLI router, TUIs, MCP
│   └── <84 snap-in tool packs>    # nmap, sqlmap, encodesk, sslprobe ... (manifest.json bricks)
├── tests/           # per-slice suites (reorganized per-module next)
├── lab/             # vulnerable target apps
└── main.py          # Docker entrypoint
suijin_agent/        # THE workspace: all output + caches/ (kb, kev)
```

Old import paths (`suijin.tools.*`, `suijin.core.*`, `suijin.helpers`,
`suijin.security`, `suijin.infra`, `suijin.nodes`, `suijin.prompts`,
`suijin.skills`, `suijin.intel`) are **gone** — the clean break landed in
v4.1 with no compatibility shims. Cross-module coupling exists only as
`requires` in manifests and Context services; the boundary test makes
the spiderweb structurally impossible.

v4.2 adds the extension ladder (skills drops -> addons -> packs ->
modules), the append-only audit trail on every surface
(`outputs/audit_trails/`, digested args — never raw values), and the
outputs consolidation (all artifacts under `outputs/`). See
`developer.md` for the full developer map.

## History

The OS was built strangler-fig over v3.3–v3.13: Phase 0 de-coupled the
legacy tree, Phase 1 landed the kernel, 1.5 the compiled core (since
RETIRED in v4.1 — the pure implementation was byte-identical and the
DAG resolves in milliseconds), 2–3 moved every subsystem onto
manifests, Phase 4 delivered the Module Manager + install system, and
the pre-release audit fixed 7 kernel bugs — including a vacuous purity
linter that had been green while hiding two real violations. v4.1
completed the modularisation: everything is a module, the old import
paths are gone, and 84 tool packs (49 legacy-converted + 35 new) give
the agent 172 tools. Full detail in CHANGELOG.md.
