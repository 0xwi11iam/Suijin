# Changelog

All notable changes to Medusa.

## [2.6.0] — 2026-08-17 — FULLY INDEXED KB + ONE WORKSPACE

### Added
- **GTFOBins fixed — KB fully indexable.** The old `GTFOBins/GTFOBins` repo is
  deleted from GitHub (codeload 404 — the source silently indexed 0 docs).
  Source now points at `GTFOBins/GTFOBins.github.io` with path-scoped patterns
  (`_gtfobins/*`); pattern matching runs against both repo-relative path and
  basename. Verified live: 478 docs indexed, `awk`/`sudo`/`shell` queries hit.
- **Alias-stub resolution (GTFOBins)** — ~20 entries are one-line stubs
  (`---\nalias: mawk\n...`); stubs are indexed with their target's full
  content + `[alias of X]` note so `source:gtfobins awk sudo` finds `awk`.
- **`search_kb` source filter + limit** — `source:<name>` in the keyword
  scopes results to one KB source (unknown sources report what's available);
  `limit` arg clamps 1-20 (default 5). Works in FTS5 and LIKE fallback.
  Documented in the tool catalog, tool registry, and MCP descriptions.
- **`medusa pull kb --status`** — offline: per-source doc counts, build date,
  age, DB size, FTS5/LIKE mode, failed sources with retry commands, and the
  ENABLED/DISABLED verdict. Pull output now ends with an explicit
  `Knowledge base ENABLED` (or `PARTIALLY ENABLED`) line.
- **`medusa selftest`** — offline smoke test (no network, no API keys):
  core imports, KB gating consistency in built+disabled states, workspace
  anchor + symlink invariant, sandbox containment, path boundary guard,
  module loading. Exits non-zero on failure.
- **Stale-KB nag** — `doctor` and `pull kb --status` warn when the build is
  older than 30 days, with the refresh command.

### Changed
- **Honest KB status** — `kb_status()` counts only sources that actually
  indexed docs (`per_source` map + `failed` map + `size_bytes` + `age_days`);
  previously it echoed the *requested* source list, hiding failures.
- **0-doc downloads are failures** — a source that downloads fine but matches
  0 files aborts with a patterns hint instead of silently shipping nothing.
  `doctor` shows the per-source doc breakdown in the KB row.
- **Resilient downloads** — 3 attempts per ref with backoff (404s skip to the
  next ref), 600 s timeout, progress logging every 50 MB, stale `.part`
  files discarded before/after every attempt (never resumed). Large sources
  (SecLists ~300 MB) log a size warning before starting; `--list` shows it.
- **One canonical `medusa_agent/` workspace** — new
  `ensure_workspace_layout()` in `tools/workspace.py` (wired into
  `tools/runtime.py` import): merges legacy real `medusa/medusa_agent/` up
  into the root workspace (legacy live data wins collisions) and replaces
  the inner path with a symlink `-> ../medusa_agent`. All 16 hard-coded
  path sites (session_replay, evidence_chain, report_exporter, audit_trail,
  goal_decomposer, failure_learner, burp_export, html_report,
  infra/workspace_fs, infra/job_runner, infra/output_offload, blue-team
  session/dossier/evidence modules, redteamer SOUL, credential_store) now
  import `WORKSPACE_DIR` from one place. Sandbox moved from
  `~/medusa_agent/sandbox` ($HOME!) to `medusa_agent/sandbox`.
  KB artifacts stay strictly in `medusa/` — never inside the workspace.
- `install.sh` and the Dockerfile create the root workspace + symlink.
- `tools/runtime.py` re-exports `DB_PATH` from `medusa/kb.py` (single owner).

### Fixed
- 190 MB stale `kb_cache/seclists.tar.part` from an aborted download —
  partials are now always cleaned up; download can never resume corrupt data.
- `burp_export`/`html_report` wrote CWD-relative paths — now anchored to
  `WORKSPACE_DIR/reports/` regardless of where medusa was launched from.

## [2.5.0] — 2026-08-17 — OFFLINE KNOWLEDGE BASE

### Added
- **`medusa pull kb`** — downloads pure-markdown/text security knowledge bases
  (HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP Cheat Sheets,
  SecLists) as GitHub tarballs and compiles them into `medusa/kb.sqlite3`
  (FTS5, porter tokenizer, BM25 ranking). The KB never ships with the repo —
  users build it on demand. Flags: `--force`, `--sources`, `--list`.
  Tarballs cache in `medusa/kb_cache/`; a failed source is skipped and
  reported (never kills the pull); compile is atomic (tmp-file replace).
- **`search_kb` upgrade** — BM25-ranked top-5 with source attribution and
  FTS5 snippets (was `LIKE '%kw%'` LIMIT 3). LIKE fallback when FTS5 is
  unavailable. Feature-gated: until the KB is built the tool reports
  DISABLED and the agent catalog lists it under a disabled section.
- **Z.ai provider** — OpenAI-compatible endpoint
  (`api.z.ai/api/paas/v4`), `ZAI_API_KEY`, default model `glm-5.3`
  (+ flash/4.7 tiers in pricing, wizard choice 5, Settings dropdown,
  `zai_model` config field). `amd` was also added to the Settings dropdown.
- **Dispatch-level safety modes** — `mode_hitl` / `mode_guardrail` are now
  enforced at the `route_tool` chokepoint (`tools/modes.py`), not just in
  the system prompt: HITL blocks non-recon tools and non-recon shell
  binaries (compound-command segments checked individually); guardrail
  blocks rm/mv/chmod/kill/etc. 20 new tests.
- Doctor: `knowledge base` row (doc count / sources / build date or build hint).

### Fixed
- `prompts/base.py` read `config.json` from CWD — safety modes silently
  no-op'd when launched outside `medusa/`. Now package-dir anchored.
- `cli.py doctor` checked the wrong config path and an impossible
  `api_key` field; keys are detected from env / `medusa/.env` (incl. `ZAI_API_KEY`).
- `agent_graph.py` NameError (`datetime`/`timezone` unimported) on the
  crash-recovery path.
- Version drift (`__init__` 1.35.0 vs cli 2.4.0) — version.json is now the
  single source, read by `medusa/__init__.py` and `cli.py`.

### Changed
- ruff: 883 -> 0 errors; CI lint is now blocking; coverage floor 35 -> 40%.
- Test deps removed from runtime `requirements.txt`;
  `duckduckgo-search` added (google_dork module dep).
- `blue_config.json` untracked (auto-generated; defaults live in code);
  `.dockerignore` no longer bakes `medusa/.env` / `config.json` into images.
- `medusa/kb.sqlite3` + `medusa/kb_cache/` are gitignored (KB never prepackaged).
- Tests: 360 -> 427 passing.
## [2.4.0] — 2026-08-13 — BACK TO ROOTS

### Removed
- **Terminal shell (`tui/`)** — the opencode-derived shell and all its
  references were scrapped. Medusa is now a single Python backend with one
  interface: the classic Rich TUI (`python3 medusa/main.py`).
- `medusa-tui.sh`, `medusa.json`, `.medusa/` (agents/commands), and the
  `tui` CI job (bun/oxlint/tsgo).

### Changed
- `medusa/tools/dispatch.py` split into 8 focused modules
  (`runtime`, `terminal`, `http_tools`, `metasploit`, `intel`, `reporting`,
  `jobs`, `aux_tools`) with a thin dispatcher and full back-compat re-exports.
- `medusa/mcp_server.py` retained as an optional headless MCP bridge.
- version.json → 2.4.0 (codename Back To Roots).

### Removed (emojis)
- Last emojis stripped from tool output strings per project style.

## [2.3.0-beta] — 2026-08-13 — SHELLFORGE (BETA)

### Added
- **Terminal shell (`tui/`)** — fork of opencode v1.18.18 (MIT, credited in
  `tui/README.md`) rebranded as Medusa: green theme, MED/USA logo, `medusa.json`
  config, `.medusa/` agent/command directories, `medusa` CLI identity.
- **MCP bridge (`medusa/mcp_server.py`)** — zero-dependency JSON-RPC stdio
  sidecar exposing the backend to the shell. Every backend tool is exposed under
  its own name (115 tools) with signature-derived input schemas; each call
  reports the tool and args used. Module packs are discovered at startup
  (`discover_modules()`).
- **`medusa-red` / `medusa-blue` agents** (`.medusa/agents/`) — primary modes
  with the ported redteamer/blueteamer doctrine; `medusa-red` is the default
  agent for new sessions (`default_agent` in `medusa.json`).
- **Shell commands** — `/classic-tui` launches the classic Rich TUI in the
  shell's terminal; `/lab` starts the vulnerable lab on :5906.
- **`medusa-tui.sh`** launcher — runs the runtime from the correct cwd and
  passes the repo root as the project directory.
- **Dual-engine CI** — `tui` job in `.github/workflows/ci.yml` (bun install,
  oxlint, tsgo typecheck) alongside the Python matrix.
- **MCP tests** (`medusa/tests/test_mcp_server.py`) — 15 tests: protocol,
  per-tool registry, named tool calls, guardrails, detection.

### Fixed
- Module tools (nmap, gobuster, sqlmap, …) were not dispatchable through the MCP
  bridge because module discovery never ran in the sidecar — real scans now
  execute and return raw output.
- JSX runtime resolution for the shell (tsconfig/node_modules cwd dependence;
  broken install after package pruning).

### Changed
- Test suite: 345 → 360 tests.
- version.json → 2.3.0-beta (codename Shellforge).
- TUI theme palette rebuilt: green base, blue/red accents.

## [2.0.2] — 2026-08-13 — STABLE

### Added
- **`SECURITY.md`** — vulnerability disclosure policy, supported versions, security model with accepted-risk table
- **`medusa/tests/test_llm_paths.py`** — 53 tests for AI-decision paths: providers pricing/usage/generate (DeepSeek success, 401/402/429, missing key, model remap, LobsterTrap routing), AI engine parsing (markdown/brace/fallback), prompt building, fail-open on API errors, action execution (commands, timeouts, code patches), oracle anomaly signals + payload mutations + hypotheses (LLM + heuristic fallback), supervisor verdicts + heuristics + cost guardrail + LLM-skip path
- **`medusa/tests/test_red_smoke.py`** — 6 tests for the full red team loop with a stubbed graph: happy-path completion, state dump, proxy config, usage reset, graph-crash handling, sync wrapper

### Fixed
- **oracle.py severity escalation bug** — `max("low", "high")` compared strings lexicographically ("low" > "high"), so high-severity signals (500s, SQL errors) were reported as low/medium. Added rank-based `_bump_severity()`.
- **redteamer.py graph-crash handling** — exceptions from the agent graph propagated out of `run_red_team_async` and killed the whole app. Now caught, reported, and the engagement ends cleanly.

### Changed
- **Test suite: 286 → 345 tests** (14 files)
- **Coverage: 35% → 40%**; CI floor raised to 35%
- version.json → 2.0.2 stable

## [2.0.1] — 2026-08-12

### Added
- **`medusa/tests/test_blueteamer.py`** — 19 tests for the Blue Team entry point (was 0% covered, now 73%): port finding, middleware snippet, firewall init (Darwin/Linux/failure paths), `_run_async` choice branches (back, invalid path, zero port, full proxy flow, full lab flow), env loading, main entry
- **`medusa/core/red/` package** — red team support modules extracted from redteamer.py:
  - `config_loader.py` (100 lines) — config.json/.env management, Pydantic validation, CI-safe env wizard
  - `llm_client.py` (46 lines) — async LLM wrapper with 90s timeout + status spinner
  - `session_control.py` (218 lines) — runtime commands (/report, /audit, /state, /sessions, /template), attack chains, objective file loading
- **Backwards-compatible re-exports** — `load_config`, `load_env`, `ENV_PATH`, `CONFIG_PATH`, `generate_async`, `_force_report`, etc. still importable from `medusa.core.redteamer`
- **`CONTRIBUTING.md`** — developer setup guide, test/lint/type-check commands, architecture overview, code style rules, commit conventions
- **`docs/adr/001-langgraph-over-asyncio.md`** — ADR: why LangGraph state machine instead of raw asyncio loop
- **`docs/adr/002-json-kg-over-neo4j.md`** — ADR: why JSON knowledge graph instead of Neo4j
- **`medusa/core/constants.py`** — centralized magic strings: model IDs, default ports (5906/8080/55553), scoring thresholds (5/6/7/8/9), timeouts, limits, deception params, blue team file paths, configurable TMP_DIR
- **`medusa/tools/guardrails.py`** — extracted from dispatch.py: 14 blocked command patterns, `is_dangerous()`, `confirm_global_action()`
- **`medusa/tools/workspace.py`** — extracted from dispatch.py: `resolve_workspace_path()` with symlink resolution, allowlist boundary checks
- **`medusa/tests/test_tools.py`** — 43 behavioral tests: all 14 blocked patterns, edge cases (case insensitivity, whitespace), workspace security (symlink bypass, allowlist, traversal), constants validation (threshold ordering, TMP_DIR env var)
- **macOS path handling** — `/private/var/tmp` added to workspace allowlist for macOS symlink resolution

### Changed
- **Constants wired into 12 files**: proxy.py, blueteamer.py, ai_engine.py, deception_engine.py, tier1_analyst.py, escalation_policy.py, subagent_manager.py, redteamer.py, knowledge_graph.py, feed.py, capture.py, dispatch.py
- **Test suite: 83 → 134 tests** (7 test files)
- README updated: accurate test counts, new file structure, pytest command, links to CONTRIBUTING.md and ADRs
- README table of contents: added Contributing + ADRs links

## [2.0.0] — 2026-08-12

### Added
- **Blue Team SOC** — autonomous defensive security agent
- **HTTP Forward Proxy** — transparent traffic interception for any app
- **18 Attack Pattern Detectors** — SQLi, XSS, SSRF, SSTI, XXE, CMDi, LFI, JWT, deserialization, LDAP, NoSQL, mass assignment, auth bypass, brute force, file inclusion, GraphQL, scanner UA
- **Per-Endpoint AI Subagents** — one per discovered endpoint, full codebase ingestion
- **Live Tarpit** — real request delays (0.018s → 5.8s) via shared state file
- **Deception Arsenal** — honeypot endpoints, canary tokens, breadcrumb trails, shadow redirect
- **25-Endpoint Vulnerable Lab** — JWT auth, SQLi, XSS, SSTI, XXE, CMDi, IDOR, SSRF, race condition
- **Session Knowledge Graph** — attackers, attacks, defenses, intelligence nodes with typed edges
- **SOC Hierarchy** — SOCLead, Tier1Analyst, Tier2Analyst, ThreatHunter, IncidentCommander
- **Structured Error Types** — BlueError, FirewallError, DeceptionError, AIEngineError, ProxyError, PatchError
- **Pydantic Config Validation** — BlueConfig (8 sub-models), RedConfig, startup validation
- **Centralized Logging** — `logging_config.py` with console + file handlers
- **pytest Framework** — `pyproject.toml`, `conftest.py`, fixtures, markers

### Changed
- Dual-mode platform: Red Team + Blue Team from single entry point
- Architecture: SOC wired into attack detection pipeline (no longer theater)
- Traffic source configurable: proxy mode, log file mode, built-in lab mode
- IP blocking disabled by default, toggle with `/block` command

### Fixed
- **26 bare `except:` clauses** eliminated — all replaced with `except Exception` + logging
- **Firewall command injection** — `ipaddress.ip_address()` validation before `sudo iptables`
- **Auth bypass regex** — now matches both HTTP header and Python dict repr formats
- **dispatch.py `_confirm_global_action`** — fixed undefined `console` global
- **Workspace path allowlist** — added `/private/tmp` for macOS compatibility
- **5 deprecated escape sequences** — all converted to raw strings
- **Duplicated `metasploit_rpc_port`** in config defaults
- **Blue team skills orphaned** — 5 skills wired into `loader.py`
- **Knowledge graphs bridged** — `bridge_from_red_team()` imports CVEs/WAF patterns
- **`apply_patch()` regex fallback** — catches f-string SQL patterns when exact match fails
- **`search_kb()` broken reference** — gracefully degrades instead of demanding nonexistent script
- **AI fail-open bug** — non-pattern requests no longer silently passed on API failure
- **Debug stderr print** in `ai_engine.py` — removed

### Removed
- Hardcoded `MEDUSA-ADMIN-2026` admin key — replaced with env var + random fallback
- Exposed API key in `opencode.json` — rotated to placeholder
- Live traffic source hardcoded to bundled lab only
- All truncation points (15 across 6 files) — AI ingests entire codebase

### Security
- Command guardrails wired to actual blocked patterns (were always returning `False`)
- Workspace path enforcement rejects absolute paths outside workspace
- IP validation before all firewall operations
- Zero bare `except:` clauses (eliminates silent failure risk)
- Prompt injection defense with cryptographic nonce wrapping

### Testing
- **74 tests** (up from 20): `test_agent_helpers.py`, `test_ai_calls.py`, `test_blue_team.py`, `test_core.py`, `test_graph.py`
- Coverage: attack patterns, knowledge graph, normalizer, dispatch guardrails, secret patterns, error types, config validation, deception engine, state machine, prompt safety

## [1.0.0] — 2026-07-26

### Added
- LangGraph-based autonomous red team agent
- 67 tools across 40 modules
- 45+ attack skills
- Parallel subagent spawning
- LLM supervisor with pattern detection
- Knowledge graph integration
- CloudBoard Next lab (15 vulnerabilities, 5 flags)
- DevOps Dashboard lab (8 vulnerabilities)
- Docker support with Kali base
