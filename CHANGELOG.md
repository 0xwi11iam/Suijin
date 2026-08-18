# Changelog

All notable changes to Suijin.

> **Note**: this project was renamed **Medusa → Suijin** at v2.12.0.
> Entries below were written under the Medusa name at the time; command and
> path examples have been updated to the new names.

## [3.4.0] — 2026-08-18 — PHASE 0 COMPLETE

Suijin OS groundwork finished — the tree is de-coupled and kernel-ready.
Behavior identical throughout; 865 tests green.

- **One job registry** (`tools/job_registry.py`): two registries existed —
  runtime.py's was dead weight (exported, never populated) while the real
  one lived as privates in nodes/execute_tool_node.py, which tools/jobs.py
  reached into. The registry is now a proper module (spawn/get/status/
  wait/output/list/cancel, capped at 200 tracked jobs); the node spawns
  through it, the job tools read through it, and the sync→background
  auto-promotion path adopts its thread into it (a ruff-caught dangling
  uuid reference exposed that path was still hand-rolling entries).
- **All 8 tools→core inversions eliminated** via `tools/services.py` — a
  stdlib service seam (proto-Context): core registers lazy producers at
  runtime-init; battle/housekeeping (blue scorer), providers (config
  loader, active model), and run_commands (audit/report/sessions) now
  import only the seam. Enforced by `test_no_core_inversions.py`, which
  AST-fails on any future tools→core import beyond core.constants.
- **Import-time mkdirs made lazy**: audit_trail and report_exporter now
  create their directories on first write; session_manager documents why
  its module-init mkdir is its documented purpose (it IS the state owner).
- mcp_server tool registry builds lazily; purity tests derive the repo
  root from `__file__` (was a hardcoded local path — CI failure class).

## [3.3.0] — 2026-08-18 — OS GROUNDWORK (PHASE 0)

First structural commit of the Suijin OS refactor — behavior identical,
851 tests green, three confirmed hazards dead:

- **God-import split**: `suijin/tools/__init__.py` made ANY tools import
  (even stdlib-only `workspace`) execute the full dispatch tree, LLM
  providers, huggingface_hub, module discovery, and a workspace
  migration. Now a PEP 562 lazy facade with submodule fallback; enforced
  by import-purity tests (`test_phase0_purity.py`) that run leaf imports
  in clean interpreters and assert nothing heavy loads.
- **Import-time side effects → `init_runtime()`**: module discovery, TLS
  suppression, workspace migration, mkdirs were import side effects of
  `tools/runtime.py`; now an explicit, idempotent, thread-safe call made
  by the entry points (cli doctor, TUI main, MCP main, test conftest).
  MCP's tool registry also builds lazily (was import-time).
- **Split-brain loader fixed**: `load_local_module` re-executed files
  without caching — providers.py ran as FIVE instances with five
  separate cost accumulators. Now one instance per file, cached under
  its canonical name so dynamic load == normal import;
  `fugu.py`'s load of a nonexistent "tools" module (a real crash) fixed
  with a direct package import.
- README: full Suijin OS architecture roadmap documented (kernel, Rust
  core, tiers, Module Manager, phase table).

## [3.2.0] — 2026-08-18 — STABLE

### Added
- **Burp-style scope TUI** (`suijin scope`, curses): include/exclude lists
  (IPs, CIDRs, hostnames), subdomain matching toggle (`*.entry` semantics),
  allow-unresolvable-hosts toggle, enforcement on/off — no policy file =
  nothing enforced. `e` toggles enforcement, `a`/`x` add include/exclude
  entries, `d` deletes, `q` saves.
- **Burp semantics in the engine**: `excluded_scopes` (exclude ALWAYS wins
  over include), `allow_subdomains` toggle (default on; off = exact
  hostnames only), explicit `*.domain` wildcard entries. DNS pinning and
  the opt-in no-file policy unchanged.
- **Operator Tools menu** (TUI option 4): scope editor, approvals console,
  battle, debrief, replay, dossier, timeline, labs, KB status, workspace
  cleaner, notify test, providers probe, panic — every feature reachable
  from the main menu instead of hidden CLI verbs.

### Fixed — found by the new tests
- **nmap parser newline swallow (pre-existing)**: `\s*` matched newlines,
  so a service line with no version remainder ("open|filtered ntp")
  consumed the NEXT line as its banner — corrupting both entries. Now
  `[ \t]*`; `open|filtered` states are captured as signal, and the full
  remainder survives in a `banner` field (NSE output never dropped).
- Directory parser now keeps `[Size: N]` when the tool prints it.
- Version drift: version.json (3.1.0) vs pyproject (3.0.0) — the packaging
  guard caught it; both synced through 3.2.0.

### Verified stable (this release's gate)
836 tests green · ruff clean · doctor/selftest pass · battle E2E (red 375,
blue 135, live block) · WebUI boot + API responses · approvals
approve→deny→clear roundtrip · packaging guard suite.

## [3.1.0] — 2026-08-18 — RUN BOX

### Added
- **Live run-command box** (`suijin/tools/run_commands.py`, wired into the
  red-team loop): an opencode-style always-on command line while the agent
  streams. Any line starting with `/` dispatches instantly without
  interrupting the run: `/state` (live agent state), `/note <text>`,
  `/kb <query>`, `/cost` (token + spend tally), `/approvals` +
  `/approve <id>` / `/deny <id>` (HITL decisions mid-run), `/scope`,
  `/audit`, `/sessions`, `/report` (saves without stopping), `/pause`
  (drops into guidance mode after the current step), `/panic`,
  `/help`. Plain text queues as operator guidance, delivered at the next
  pause. Daemon-thread reader; dispatches are fully guarded (a broken
  command can never break the run); silent when stdin is a pipe (CI);
  stop() at every loop exit. Standalone module — no redteamer imports
  (modular-ready).
- **Subagent system tests** (`test_subagents.py`, 12): AI-analysis path
  (prompt carries real handler source; all five engineering outputs
  parsed), fallback path via real source files (SQLi/command-injection/
  sensitive-path scoring), batch analysis with crash isolation (one
  exploding subagent can't kill the batch), anomaly/block counters,
  notes rendering, risk-ordered summaries, and the exact
  deploy→analyze→route sequence blueteamer drives.

### Fixed
- **HITL approvals gap**: `execute_terminal` calls blocked by the recon
  binary allowlist never queued an approval request — the operator
  couldn't see or approve blocked commands. They now queue with the
  offending binary recorded in the args (`blocked_binary`), and the block
  message points at `suijin approvals`. Approving the request lets the
  tool through for the session; denying produces an explicit DENIED
  message on retry.
- RunBox guidance queue printed a pending count by draining the queue it
  was reporting (count now read under the same lock, queue intact).
- `/kb` output had `[hacktricks]`-style source tags eaten by Rich markup
  interpretation — escaped now.

## [3.0.0] — 2026-08-18 — THE RENAME RELEASE

### Added — packaging
- **Installable Python package**: `[project]` table with pinned deps, a
  `suijin` console script (`suijin.cli:main`), and package-data for every
  runtime asset (version/config/prompts/skills/tutorials/ui dist). `pipx
  install suijin` / `uv tool install suijin` now work; the wheel is
  verified complete by tests (no tests shipped, entry point live).
  Packaging guards lock pyproject↔version.json (the 2.3.0-beta drift
  class), verify every package-data glob matches real files (caught a
  stale templates/* on day one), and require requirements.txt deps to be
  declared.

### Added — safety & resilience (from the core feature spec)
- **HITL approvals console** (`suijin approvals list/approve/deny/clear`):
  blocked HITL tool calls are recorded as pending requests; approving
  allows that tool for the session (honored inside modes.py), denying
  hard-blocks it with an explicit message; latest decision wins; the
  request log survives `clear`. File-based, no daemons, no TTL races.
- **Panic button** (`suijin panic`): kills Suijin-owned processes
  (TUI/web/labs/scanners via narrow pkill patterns) and clears blue-team
  live state in /tmp. Best-effort by design — a panic command must never
  itself fail. `--dry-run` previews.
- **DNS-pinned scope enforcement**: a hostname in the allowed scopes must
  ALSO resolve to in-scope IPs — `example.com` scoped while its DNS
  points elsewhere is now blocked, with the offending IPs named.
  Unresolvable hosts fail CLOSED unless the policy sets
  `allow_unresolvable`. Resolution is memoized per host.
- **Self-healing tool execution**: route_tool retries network-shaped
  failures (timeout/connection/5xx) up to twice with backoff — the same
  call unchanged, because those errors are environmental. Logical errors
  return immediately so the agent adjusts next turn instead of burning
  the clock on guaranteed-failing retries.
- **Output normalizer** (`normalize_output` agent tool): compact JSON
  from the two noisiest outputs — nmap service tables (port/proto/
  service/product/version) and directory brute-force lists (path/status,
  2xx-3xx only). Auto-detects which parser fits.

### Fixed — bugs found by the new tests
- Codebase scanners excluded files by SUBSTRING over the whole path:
  any project under a directory containing `test_` (pytest tmp dirs,
  `contest_app`) silently lost every route. Now matches path parts /
  filename prefixes (python, JS, and PHP analyzers).
- install.sh migration block called `info` before the function existed
  (found by the migration E2E); block moved below the helper defs.

### Added — tests & coverage
- 49 new tests (safety/resilience, coverage push on subagent_manager,
  codebase scanners, session_control; packaging guards; Medusa→Suijin
  migration E2E incl. a full install.sh run against a fake $HOME with a
  legacy ~/.medusa — marked `slow`, CI runs it).
- Coverage 54% → **60%**; gate raised 48 → 56.

### Not built (asset/liability filter — see 2.11.0's list)
- Per-tool output parsers beyond nmap/dirs (LLM reads text fine; offload
  handles size), rollback manager, team sync, post-exploit cleanup
  orchestrator — each would add state machines with no current consumer.

## [2.12.0] — 2026-08-18 — SUIJIN (FULL PRODUCT RENAME)

### Changed — Medusa is now Suijin, everywhere
- **Package**: `medusa/` → `suijin/` (git-tracked rename; history kept).
  Every `from medusa…`/`import medusa` across code, tests, CI, Docker, and
  docs now targets `suijin`.
- **CLI**: the `medusa` command is now `suijin` (`suijin doctor`,
  `suijin pull kb`, `suijin ui`, …). argparse prog, help text, and every
  doc example updated.
- **Workspace**: `medusa_agent/` → `suijin_agent/` with automatic data
  migration — `ensure_workspace_layout()` renames (or merges, when both
  exist) a legacy `medusa_agent/` root, merges legacy inner real dirs,
  and removes stale legacy symlinks. Existing engagements, reports, and
  audit trails carry over untouched.
- **Installer**: installs to `~/.suijin` with a `suijin` launcher;
  migrates a legacy `~/.medusa` on first install; `MEDUSA_*` env
  overrides still honored. `MEDUSA_TMP_DIR` still respected as a
  fallback to `SUIJIN_TMP_DIR`.
- **WebUI**: title, branding, snapshot version source, and the committed
  bundle path (`suijin/ui/dist`) — rebuilt and committed; the CI
  freshness gate checks the new path.
- **MCP sidecar**: server name `suijin`; version sourced from
  version.json.
- Docs: README (rename note at top), CONTRIBUTING, SECURITY, ADRs,
  tutorials, module skills — all references updated.
- GitHub repo rename handled by the maintainer; install URLs point at
  `0xwi11iam/Suijin`.

### Tests
- New legacy-workspace migration tests (rename root, merge when both
  exist, stale legacy inner symlink cleanup).

## [2.11.2] — 2026-08-18 — DEAD-CODE SWEEP (73 MODULES)

### Removed
Deleted only what an AST import-graph proved unreachable from every entry
point (main, cli, mcp_server, kb, ui/server) AND every test. Dynamic
string references audited separately (module-loader scans Modules/ only;
`blue_hotfix` in skills/loader is a dict key bound to the live
skills/blue_patching prompt; tutorials .md files are read by path).

- **6 whole blue packages** (all modules dead):
  `core/blue/counter_intel/` (5), `endpoints/` (3), `forensics/` (5),
  `hotfix/` (3, after 2.11.1's two), `intel/` (6), `response/` (5) —
  marketing-tree stubs never wired into the pipeline.
- **Dead files in live blue packages**: deception (breadcrumb_layer,
  misinformation, phantom_endpoint), defense (misinformation,
  rate_limiter, session_revoker, waf_rules), soc/shift_manager, traffic
  (capture, classifier, rate_tracker), tui (alert_panel, dashboard,
  metrics), watchers (health_monitor, load_balancer, result_collector,
  watcher_protocol, watcher_roles), core/blue/orchestrator.py.
- **4 stub blueteam nodes** (nodes/blueteam_*.py — 200-byte no-ops;
  the blue graph never registered them).
- **4 dead blue prompts** (blue_base, blue_hotfix, blue_tool_registry,
  blue_watcher; blue_system is the live one).
- **9 orphaned tools**: blue_utils, confidence, cvss_scorer,
  evidence_chain, failure_learner (never dispatched — failure_db.json had
  no writer), goal_decomposer, har_replay, hotreload_skills,
  timeline_viz.
- **Duplicates**: core/paths.py (SUIJIN_TMP_DIR logic lives in
  constants.py), logging_config.py, tutorials/__init__.py (markdown
  tutorials stay, read by path).
- **`mode_hotreload_skills` config flag** — its only implementing module
  was dead; removed from Settings TUI, config.json, README.

### Added
- **`test_import_graph.py`** — regression guard: parses every live
  file's imports (including relative-import level semantics) and fails
  on any dangling `suijin.*` reference; entry points asserted
  importable; pruned packages asserted gone. Caught and fixed its own
  resolver bug during development (level handling for package inits).

740 tests green; ruff clean; entry points, doctor, selftest, status all
smoke-verified post-sweep.

## [2.11.1] — 2026-08-18 — DEEP-TEST PASS

### Added — 50 tests over live low-coverage modules
- **Red knowledge graph** (`test_red_knowledge_graph.py`, 19): constraint
  dedupe with evidence/confidence max-merge, check_payload case-insensitive
  substring blocking + non-string/empty-rule safety, CVE/bypass queries,
  summary formatting (partial-confidence annotation only), corrupt-JSON
  resilience and recovery-on-write, and the real agent surface
  (record_finding → check_knowledge roundtrip, invalid finding types).
- **Infrastructure & defense** (`test_infra_and_defense.py`, 17): output
  offload (never-policy passthrough, threshold boundary, file write +
  500-char preview + ellipsis), firewall (IP validation BEFORE any
  subprocess call, block/unblock rule ops, DROP-line filtering), traffic
  tailing (append, rotation/truncation reset, late-appearing file),
  msf_check (RPC / console-fallback / not-detected).
- **Session-awareness + http_request** (`test_http_session_tools.py`, 14):
  Set-Cookie + CSRF extraction, auth detection, RateLimitTracker (429 +
  Retry-After, low-remaining throttle, domain isolation, window expiry),
  UA rotation, and the tool surface with a mocked transport (status
  render, browser-mimicry defaults, RATE LIMITED short-circuit, transport
  errors, body forwarding).

### Fixed — bugs the new tests caught
- **SessionState replayed cookie attributes as cookies**: `Set-Cookie:
  sid=abc; Path=/; HttpOnly` stored `Path=/` as a cookie and sent it on
  every subsequent request. Only the first `;`-segment (the actual
  cookie pair) is parsed now.
- **Removed orphaned broken hotfix modules**: patch_generator.py +
  silent_patch.py had zero importers and emitted syntactically invalid
  patches (`escape(render(x)` — unbalanced parens; the "sqli fix" also
  stripped every `f"` in the file). Deleted rather than blessed with
  tests. (~70 other 0%-coverage blue modules audited: also orphaned,
  noted for a future sweep — none are on live paths.)

## [2.11.0] — 2026-08-18 — TRUST BUT VERIFY

### Added — hardening
- **CLI tests for every v2.10 verb** (`test_cli_v210.py`, 30 tests): exit
  codes, arg errors, output shape for kb diff/read, pull cve, creds
  (init/list/add/get/export with mocked passphrase), dossier, timeline,
  watch, clean (dry-run vs apply), rules, policy, providers, module,
  notify. **Caught a real bug**: run_watch passed the list-based
  traffic enricher as a per-entry function — it crashed on the first
  live line at runtime. Fixed with a per-entry adapter.
- **Frontend CI gate**: new `webui` job — node 20, `npm ci && npm run
  build`, then a git-diff freshness check on `suijin/ui/dist`: a stale
  committed bundle fails the build (permanently closes the v2.9.2-class
  regression). Build verified byte-deterministic locally.
- **Coverage floor** 40 → 48 (measured 52%, 4-point buffer).

### Added — WebUI
- **Dossier view**: target search → constraint/failure/history/report
  cards with richness count (`/api/dossier?target=`).
- **Timeline view**: day-grouped unified feed across audits, sessions,
  and reports with kind-colored badges (`/api/timeline?limit=`).
- KEV mirror count in `/api/overview` + Settings KB tab. 9 new backend
  tests; dist rebuilt and committed.

### Added — compliance mapping
- **`suijin compliance [engagement]`** (`tools/compliance.py`): findings
  mapped to CWE / OWASP Top-10 2021 / MITRE ATT&CK via a pure keyword
  lookup (snake_case finding types normalized; specific rows before
  generic; unmapped fall back to CWE-693). Per-finding table + per-
  framework summaries. Standalone by design — no report-pipeline
  changes, no state. 22 tests.

### Deliberately NOT built (over-engineering review)
- HITL approvals queue (hot-path + TTL state + coordination risk)
- Battle live-view heartbeat file (stale-state trap; reports suffice)
- Skill golden-set evals (keyword scoring = misleading signal)
- Custom detector rules in the production path (defense drift risk)

## [2.10.0] — 2026-08-18 — FULL ARSENAL (20 FEATURES)

### Added — knowledge & intel
- **KB v2**: `suijin kb read <path>` dumps full untruncated documents from
  the cached tarballs (the FTS copy is 256k-capped); substring paths and
  cross-source ambiguity handling; agent tool `kb_read`. `suijin kb diff`
  reports per-source index-vs-cache staleness (newer tarball → rebuild,
  unindexed cache → pull). `suggest_exploit` fuzzy-matches GTFOBins bins
  (difflib, cutoff 0.75 — `finnd` → `find`).
- **CISA KEV mirror** (`suijin pull cve`, no API key): 24h-cached catalog
  in `suijin/cve_cache/`; `search_cve` falls back to it offline with
  `[KEV offline]` attribution when NVD is unreachable.
- **Recon auto-suggest**: `recon_chain` appends offline exploit leads
  (GTFOBins/HackTricks/PayloadsAllTheThings) for fingerprinted services.

### Added — operator commands
- **Credential vault** (`suijin creds init|list|add|get|export`):
  PBKDF2-HMAC-SHA256 keystream encryption + HMAC tag at rest (stdlib
  only), file perms 0600, imports AND SHREDS legacy credentials.json,
  redacted exports by default.
- **Target dossiers** (`suijin dossier <target>` + `target_dossier` agent
  tool): merges red-KG constraints, failure_db, audit mentions, and report
  mentions into one per-target profile.
- **`suijin timeline`**: unified chronological view across audits,
  sessions, and reports.
- **`suijin watch`**: live tail of the traffic log with per-line scoring
  (same tier semantics as the TUI).
- **`suijin clean`**: workspace cleaner — dry-run by default, `--apply`
  archives stale outputs/sandbox to a zip then deletes.
- **`suijin providers`**: live provider probe (tiny request, latency +
  error report); `--all` probes every keyed provider.
- **`suijin notify`**: operator notifications (macOS / arbitrary command
  / file channels) — battle mode fires on flag captures and network
  blocks.
- **`suijin labs run`**: boots and probes every lab (reachability, landing
  flags, route hints, latency) → capability-matrix baseline.

### Added — governance (opt-in)
- **Policy engine** (`suijin/policy.json` + `suijin policy check|show`):
  blocked tools, blocked arg regexes, allowed target scopes (IPs/CIDRs/
  hostnames) enforced at the route_tool chokepoint. NO FILE = NO
  ENFORCEMENT (existing engagements untouched); intel-only tools are
  scope-exempt by design.
- **Custom detector rules** (`suijin/detector_rules.json` + `suijin rules
  validate|list`): regex detectors (field: body/path/ua/headers, weight
  1-10) loaded by the eval harness and battle watchdog; linted for regex/
  schema errors.

### Added — extensibility & resilience
- **Module SDK** (`suijin module init|validate`): scaffolds a working pack
  (manifest + implementation + skill doc); validation checks manifest
  schema, imports main.py, and verifies every declared tool is a callable
  with a docstring.
- **Skill versioning** (`suijin skills history|diff|rollback`): every
  edit_skill write snapshots the prior version (nanosecond-named, capped
  at 25/skill) into suijin_agent/skill_history/.
- **Provider failover**: `fallback_providers` config list honored via
  generate_with_failover (hard errors roll to the next provider; successes
  short-circuit); wired into llm_client.
- **Wordlist engine**: `mutate_wordlist` agent tool (leet/years/suffixes/
  prefixes, 50k cap) and `cewl_words` (harvest wordlists from fetched
  pages, script/style stripped).

### Tests
- 55 new tests (`test_kb_v2_and_intel.py` + `test_v210_features.py`);
  620 total, all offline.

## [2.9.2] — 2026-08-18 — DEAD-CODE SWEEP + WEBUI OVERHAUL

### Fixed — WebUI bugs
- **Activity feed flooded with duplicates** — every 3 s SSE snapshot
  re-appended the same tail entries forever. Feed is now delta-based:
  first paint seeds the tail once, then only genuinely new entries append
  (capped at 100).
- **ForceGraph re-render storm** — the physics effect depended on hover
  state, so every mousemove tore down and restarted the simulation, and
  setHover inside the rAF loop caused re-render loops. All interaction
  state now lives in refs; tooltips draw directly on the canvas; the
  effect runs once. Node radius now scales with graph degree.
- **Detector grid never lit up** — labels ("SQL Injection") were matched
  against KG attack types ("sql_injection"): space vs underscore, always
  false. Detectors now declare explicit signal keys and count real hits
  from a new `signal_counts` snapshot field (live traffic signals) merged
  with `blue_kg.attack_type_counts`.
- **Radar was placeholder data** — axes now derive from actual detector
  signal counts + blue-KG attack types.
- **Mobile nav was unreachable** — sidebar was display:none under 768px
  with no way to open it. Hamburger button + slide-in drawer with
  backdrop; nav links close it on tap.
- Polish: severity-railed traffic rows (red/amber/green left edges),
  hero-card accent underlines, path overflow ellipsis, traffic rows show
  triggering signals, rate sparkline on the monitored-requests card.

### Removed — dead code (the class of bug behind the 2.3.0-beta drift)
- `mcp_server.SERVER_VERSION` hardcoded "2.3.0-beta" (five releases of
  drift) — now sourced from version.json like everything else.
- `tools/runtime.py`: dead `MCP_SERVERS` / `get_server_for_tool` /
  `AI_SERVICE_ENDPOINTS` / `fingerprint_ai_response` stubs and their
  dispatch re-exports + tests (dispatch still re-exports the live ones).
- `tools/providers.py`: dead `_get_config_path`/`_load_config` (generated()
  with no config now uses the canonical config_loader).
- `core/templates.py`: unused `validate_config` + REQUIRED/OPTIONAL_KEYS
  tables (Pydantic RedConfig has been the real validator since 2.5).
- Zombie config keys with zero consumers: `use_database_framework`,
  `use_local_bin_folder`, `agent_workspace`, `report_auto_export`,
  `report_format` — removed from default config, Settings TUI, and docs.
- `intel/oracle.py`: unreachable code after return (the intended
  "lean safe" default was dead); B007/B904 lint batch across 9 files.

### Added
- `signal_counts` + `blue_kg.attack_type_counts` in the UI snapshot
  (aggregated detector signals across the traffic window — works with or
  without an active blue session). 2 new backend tests; 565 total.

## [2.9.1] — 2026-08-18 — PROVIDER-AWARE MODEL DISPLAY

### Fixed
- **Status lines showed the wrong model** — the launcher banner and the
  `Thinking... (zai/deepseek-ai/DeepSeek-V4-Flash)` spinner hardcoded
  `final_model_id` (a HuggingFace-style id written by the default config)
  as the cross-provider fallback, so it always won over `<provider>_model`.
  New `active_model()` helper (core/red/config_loader.py) resolves the
  model per provider; wired into redteamer's launcher line, llm_client's
  spinner, the blue AI engine's `result.llm_model` record, the LobsterTrap
  forwarder, and the AMD branch (which now also honors an `amd_model` key).
  HuggingFace keeps `final_model_id` — that's the only provider it means
  anything for. The actual API calls were always correct; only display and
  record-keeping lied. 6 regression tests.

## [2.9.0] — 2026-08-18 — PURPLE ARENA

### Added
- **`suijin export`** — chain-of-custody evidence bundles
  (`tools/export_bundle.py`): zip of reports, audit trails, sessions, blue
  state, dossiers, both knowledge graphs + redacted config. Every file
  SHA-256-hashed in `manifest.json`; `custody.json` records when/host/
  commit. `--verify <zip>` re-hashes and flags mismatches, missing, or
  unlisted files (tamper + smuggling detection). Credentials excluded
  unless `--with-creds`.
- **`suijin debrief`** — engagement analytics over audit trails
  (`tools/debrief.py`): per-engagement table (actions/ok/fail/findings/
  cost/duration), fleet trends (avg duration, findings per engagement, top
  tools), `-v` per-engagement severity + tool-success breakdowns.
- **`suijin replay`** — interactive engagement timeline (`tools/replay.py`):
  Rich Live panes (thought / action+args / observation), space play-pause,
  arrows scrub, +/- speed, up/down 10-step jumps. `--list`, `--file`,
  `--export-md` full transcript; non-TTY prints the transcript directly.
- **`suijin eval`** — detector tuning harness
  (`core/blue/traffic/replay_harness.py`): replays recorded traffic
  through the REAL production scorer, labels entries via strong heuristic
  rules or `labels.jsonl` overrides, reports precision/recall/F1 at the
  production threshold + full sweep + best operating point. Unlabeled
  entries are excluded, never silently benign.
- **`suijin battle`** — purple-team mode (`tools/battle.py`): boots the
  blue_target lab fresh, runs a scripted red campaign (recon → auth →
  access → injection chain → sweep; 12 attack classes, flag capture) while
  a BlueWatchdog tails the live traffic log, scores with the production
  scorer, and deploys real defenses — tarpits written to the file the LAB
  enforces (measurable latency), blocks that deny later red requests.
  Live scoreboard, markdown battle report in suijin_agent/reports/.
  Scoring: red 100/flag + 25/class; blue 10/detect + 25/tarpit + 50/block.
- 28 new tests across `test_export_debrief_replay.py` and
  `test_eval_battle.py`.

### Fixed — real detector gaps found by the new harness
- `anomaly_detector.detect_anomalies` scanned ONLY the request body:
  query-string attacks (`?data={{...}}`, `?path=../../`, GraphQL recon)
  were invisible. Now scans body + query + path. Production recall on
  battle traffic: 0.14 → 0.57 at threshold 5 (0.86 at threshold 2),
  precision held ≥ 0.80.
- XXE bodies (`<!ENTITY`) and privilege-spoofing headers (`X-Admin: true`,
  `X-Role: admin`) were never inspected — both now signal at weight 5.
- Battle-time effect: blue score vs the same scripted campaign went
  35 → 135 with an actual network block landing mid-campaign.

## [2.8.0] — 2026-08-17 — ABYSS CONSOLE (WEB DASHBOARD)

### Added
- **`suijin ui`** — local-first web dashboard for the operator:
  - **Frontend**: React 18 + TypeScript + Vite single-page app in `webui/`
    (sources) built to `suijin/ui/dist/` (committed — works without Node).
    "Abyss" design system: glass-morphism cards, neon-accent interactions,
    no shadows/eyebrow-lines, Gotham Medium (Montserrat stand-in — Gotham is
    commercial), Instrument Serif display stats, JetBrains Mono terminals.
    Dark theme only, responsive (12-col → 2-col → 1-col).
  - **Views**: Dashboard (hero stats, canvas attack map with animated
    vectors spawned by suspect traffic, attack-pattern radar, live activity
    feed, lab fleet liveness), Red Team (stage-derived pipeline flow,
    engagement log/findings, tool arsenal with availability, stat cards),
    Blue Team (three-tier traffic monitor, 18-detector grid, tarpit
    controls + tarpitted-IP table, KG summary), Knowledge Graph (hand-rolled
    force-directed physics, blue/red sources, node inspector), Labs (live
    port probes + copy-to-clipboard attack commands), Reports (audit
    summaries + file browser), Settings (redacted config, KB inventory,
    design tokens).
  - **Backend** (`suijin/ui/server.py`): Flask + SSE, zero new Python deps.
    `/api/events` pushes full snapshots every 3 s (leading frame on
    connect, keepalives); REST: overview, kb/search, report, session,
    config. Traffic entries enriched server-side with the REAL
    `anomaly_detector` so tiers match the TUI exactly. Report/session reads
    are workspace-confined (traversal 404s); config values matching
    key/token/secret/password redacted. Bound to 127.0.0.1 ONLY.
    CLI: `suijin ui [--port] [--no-open]`; `npm run dev` in `webui/` proxies
    `/api` for hot-reload development.
  - 17 backend tests (`test_ui_server.py`).
- KB agent toolkit + phrase queries (landed earlier in the 2.8 window):
  suggest_exploit, find_wordlist, extract_payloads, kb_stats, wordlist_tool,
  mine_failures, anonymize_report; `'"union select"'` ordered-phrase FTS5.

### Fixed
- WebUI snapshot resolved `suijin/` paths from the repo root (labs list,
  provider config, red KG all silently empty) — PKG_DIR now anchored to the
  package dir. SPA fallback no longer masks missing assets with index.html.

## [2.7.0] — 2026-08-17 — OPERATORS CLI + KB TOOLKIT

### Added
- **KB-powered agent toolkit** (`tools/kb_tools.py`, 7 new offline tools):
  `suggest_exploit` (fingerprint → GTFOBins privesc page + HackTricks +
  PayloadsAllTheThings leads), `find_wordlist` (SecLists keyword search that
  **materializes** files into `suijin_agent/wordlists/` from the cached
  tarball — the DB copy can be truncated), `extract_payloads` (KB code
  blocks → `suijin_agent/payloads/`, 8-16k size window), `kb_stats`
  (inventory), `wordlist_tool` (merge/dedupe/length-filter), `mine_failures`
  (SequenceMatcher clustering of `failure_db.json`), `anonymize_report`
  (regex scrubber for IPs/emails/bearer tokens/api keys/JWTs/private keys →
  `suijin_agent/reports/anonymized/`, localhost + FLAG{} preserved). Wired
  into dispatch routes, tool catalog (KB-dependent tools gated on the build),
  tool registry, MCP descriptions, and the HITL allowlist. 21 tests.
- **Phrase queries in search_kb** — quoted spans become ordered FTS5
  phrases: `'"union select"'` matches adjacent words only (`select ... union`
  no longer matches). Unquoted keywords keep implicit-AND semantics.

### Added
- **12 new non-interactive CLI commands** — every one offline, scriptable,
  exit 0 on healthy: `suijin status` (one-page summary), `version`, `env`
  (API key presence, names only — values never printed), `tools` (all tools
  with availability marks), `modules` (packs + deps), `skills`,
  `config show` (effective config, secrets redacted), `config validate`
  (Pydantic, exit 1 on failure), `workspace` (layout + usage + symlink
  health), `reports` (newest-first listing), `sessions` (with objectives),
  `labs` (real ports + launch commands, scanned live from `suijin/lab/`).
  Bare `suijin pull` / `suijin config` now print help instead of silently
  doing nothing. 23 new tests in `test_cli_commands.py`.
- **Z.ai dual-endpoint selection** — `zai_endpoint` config picks the billing
  surface: `"coding"` **(default)** = GLM Coding Plan subscription endpoint
  (`https://api.z.ai/api/coding/paas/v4`, burns plan credits, Lite/Pro/Max
  quotas, models glm-5.3 / glm-5-turbo / glm-4.7 with older ids auto-routed)
  or `"paas"` = pay-as-you-go endpoint (`https://api.z.ai/api/paas/v4`,
  per-token USD). Full custom base URLs (proxies) also accepted. Previously
  the provider hardcoded the pay-as-you-go endpoint — Coding Plan
  subscribers burned nothing and got errors. A plan key on the wrong surface
  now gets a 403 message naming both endpoints and the exact fix (no blind
  retries). Exposed in the Settings TUI (`zai_endpoint` picker, zai-only),
  `RedConfig` validation (rejects typos like "free-tier"), constants
  (`ZAI_ENDPOINT = "coding"`), and shown by `suijin doctor` / `suijin
  status`. 27 tests in `test_zai_provider.py` (endpoint selection incl.
  case-insensitivity + unknown-value fallback to coding, URL constants
  pinned to Z.ai docs, 403 guidance, glm-5-turbo pricing, doctor row).
- `suijin doctor` gained a `workspace` row (canonical dir + symlink health).

### Fixed
- **DeepSeek timeout fall-through** — after exhausting retries the DeepSeek
  branch fell through to `Error: Unknown provider 'deepseek'` instead of
  returning `Error: DeepSeek API Timeout` (regression test added).

### Changed
- **README rewritten documentation-first** — 1,429 marketing-heavy lines →
  ~600 lines of reference: full CLI table, configuration key reference,
  provider docs (incl. the Z.ai coding/paas explainer), KB / workspace /
  architecture / red / blue reference sections, real labs table (the old
  README documented a `cloudboard_next` lab that does not exist in the
  repo — replaced with the 8 actual labs and their real ports), trimmed
  troubleshooting/glossary, credits reduced to one line each.
- Z.ai model pickers updated to the Coding Plan catalogue
  (glm-5.3 / glm-5-turbo / glm-4.7 / glm-5.1 / glm-4.6).

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
- **`suijin pull kb --status`** — offline: per-source doc counts, build date,
  age, DB size, FTS5/LIKE mode, failed sources with retry commands, and the
  ENABLED/DISABLED verdict. Pull output now ends with an explicit
  `Knowledge base ENABLED` (or `PARTIALLY ENABLED`) line.
- **`suijin selftest`** — offline smoke test (no network, no API keys):
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
- **One canonical `suijin_agent/` workspace** — new
  `ensure_workspace_layout()` in `tools/workspace.py` (wired into
  `tools/runtime.py` import): merges legacy real `suijin/suijin_agent/` up
  into the root workspace (legacy live data wins collisions) and replaces
  the inner path with a symlink `-> ../suijin_agent`. All 16 hard-coded
  path sites (session_replay, evidence_chain, report_exporter, audit_trail,
  goal_decomposer, failure_learner, burp_export, html_report,
  infra/workspace_fs, infra/job_runner, infra/output_offload, blue-team
  session/dossier/evidence modules, redteamer SOUL, credential_store) now
  import `WORKSPACE_DIR` from one place. Sandbox moved from
  `~/suijin_agent/sandbox` ($HOME!) to `suijin_agent/sandbox`.
  KB artifacts stay strictly in `suijin/` — never inside the workspace.
- `install.sh` and the Dockerfile create the root workspace + symlink.
- `tools/runtime.py` re-exports `DB_PATH` from `suijin/kb.py` (single owner).

### Fixed
- 190 MB stale `kb_cache/seclists.tar.part` from an aborted download —
  partials are now always cleaned up; download can never resume corrupt data.
- `burp_export`/`html_report` wrote CWD-relative paths — now anchored to
  `WORKSPACE_DIR/reports/` regardless of where suijin was launched from.

## [2.5.0] — 2026-08-17 — OFFLINE KNOWLEDGE BASE

### Added
- **`suijin pull kb`** — downloads pure-markdown/text security knowledge bases
  (HackTricks, PayloadsAllTheThings, GTFOBins, LOLBAS, OWASP Cheat Sheets,
  SecLists) as GitHub tarballs and compiles them into `suijin/kb.sqlite3`
  (FTS5, porter tokenizer, BM25 ranking). The KB never ships with the repo —
  users build it on demand. Flags: `--force`, `--sources`, `--list`.
  Tarballs cache in `suijin/kb_cache/`; a failed source is skipped and
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
  no-op'd when launched outside `suijin/`. Now package-dir anchored.
- `cli.py doctor` checked the wrong config path and an impossible
  `api_key` field; keys are detected from env / `suijin/.env` (incl. `ZAI_API_KEY`).
- `agent_graph.py` NameError (`datetime`/`timezone` unimported) on the
  crash-recovery path.
- Version drift (`__init__` 1.35.0 vs cli 2.4.0) — version.json is now the
  single source, read by `suijin/__init__.py` and `cli.py`.

### Changed
- ruff: 883 -> 0 errors; CI lint is now blocking; coverage floor 35 -> 40%.
- Test deps removed from runtime `requirements.txt`;
  `duckduckgo-search` added (google_dork module dep).
- `blue_config.json` untracked (auto-generated; defaults live in code);
  `.dockerignore` no longer bakes `suijin/.env` / `config.json` into images.
- `suijin/kb.sqlite3` + `suijin/kb_cache/` are gitignored (KB never prepackaged).
- Tests: 360 -> 427 passing.
## [2.4.0] — 2026-08-13 — BACK TO ROOTS

### Removed
- **Terminal shell (`tui/`)** — the opencode-derived shell and all its
  references were scrapped. Suijin is now a single Python backend with one
  interface: the classic Rich TUI (`python3 suijin/main.py`).
- `suijin-tui.sh`, `suijin.json`, `.suijin/` (agents/commands), and the
  `tui` CI job (bun/oxlint/tsgo).

### Changed
- `suijin/tools/dispatch.py` split into 8 focused modules
  (`runtime`, `terminal`, `http_tools`, `metasploit`, `intel`, `reporting`,
  `jobs`, `aux_tools`) with a thin dispatcher and full back-compat re-exports.
- `suijin/mcp_server.py` retained as an optional headless MCP bridge.
- version.json → 2.4.0 (codename Back To Roots).

### Removed (emojis)
- Last emojis stripped from tool output strings per project style.

## [2.3.0-beta] — 2026-08-13 — SHELLFORGE (BETA)

### Added
- **Terminal shell (`tui/`)** — fork of opencode v1.18.18 (MIT, credited in
  `tui/README.md`) rebranded as Suijin: green theme, MED/USA logo, `suijin.json`
  config, `.suijin/` agent/command directories, `suijin` CLI identity.
- **MCP bridge (`suijin/mcp_server.py`)** — zero-dependency JSON-RPC stdio
  sidecar exposing the backend to the shell. Every backend tool is exposed under
  its own name (115 tools) with signature-derived input schemas; each call
  reports the tool and args used. Module packs are discovered at startup
  (`discover_modules()`).
- **`suijin-red` / `suijin-blue` agents** (`.suijin/agents/`) — primary modes
  with the ported redteamer/blueteamer doctrine; `suijin-red` is the default
  agent for new sessions (`default_agent` in `suijin.json`).
- **Shell commands** — `/classic-tui` launches the classic Rich TUI in the
  shell's terminal; `/lab` starts the vulnerable lab on :5906.
- **`suijin-tui.sh`** launcher — runs the runtime from the correct cwd and
  passes the repo root as the project directory.
- **Dual-engine CI** — `tui` job in `.github/workflows/ci.yml` (bun install,
  oxlint, tsgo typecheck) alongside the Python matrix.
- **MCP tests** (`suijin/tests/test_mcp_server.py`) — 15 tests: protocol,
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
- **`suijin/tests/test_llm_paths.py`** — 53 tests for AI-decision paths: providers pricing/usage/generate (DeepSeek success, 401/402/429, missing key, model remap, LobsterTrap routing), AI engine parsing (markdown/brace/fallback), prompt building, fail-open on API errors, action execution (commands, timeouts, code patches), oracle anomaly signals + payload mutations + hypotheses (LLM + heuristic fallback), supervisor verdicts + heuristics + cost guardrail + LLM-skip path
- **`suijin/tests/test_red_smoke.py`** — 6 tests for the full red team loop with a stubbed graph: happy-path completion, state dump, proxy config, usage reset, graph-crash handling, sync wrapper

### Fixed
- **oracle.py severity escalation bug** — `max("low", "high")` compared strings lexicographically ("low" > "high"), so high-severity signals (500s, SQL errors) were reported as low/medium. Added rank-based `_bump_severity()`.
- **redteamer.py graph-crash handling** — exceptions from the agent graph propagated out of `run_red_team_async` and killed the whole app. Now caught, reported, and the engagement ends cleanly.

### Changed
- **Test suite: 286 → 345 tests** (14 files)
- **Coverage: 35% → 40%**; CI floor raised to 35%
- version.json → 2.0.2 stable

## [2.0.1] — 2026-08-12

### Added
- **`suijin/tests/test_blueteamer.py`** — 19 tests for the Blue Team entry point (was 0% covered, now 73%): port finding, middleware snippet, firewall init (Darwin/Linux/failure paths), `_run_async` choice branches (back, invalid path, zero port, full proxy flow, full lab flow), env loading, main entry
- **`suijin/core/red/` package** — red team support modules extracted from redteamer.py:
  - `config_loader.py` (100 lines) — config.json/.env management, Pydantic validation, CI-safe env wizard
  - `llm_client.py` (46 lines) — async LLM wrapper with 90s timeout + status spinner
  - `session_control.py` (218 lines) — runtime commands (/report, /audit, /state, /sessions, /template), attack chains, objective file loading
- **Backwards-compatible re-exports** — `load_config`, `load_env`, `ENV_PATH`, `CONFIG_PATH`, `generate_async`, `_force_report`, etc. still importable from `suijin.core.redteamer`
- **`CONTRIBUTING.md`** — developer setup guide, test/lint/type-check commands, architecture overview, code style rules, commit conventions
- **`docs/adr/001-langgraph-over-asyncio.md`** — ADR: why LangGraph state machine instead of raw asyncio loop
- **`docs/adr/002-json-kg-over-neo4j.md`** — ADR: why JSON knowledge graph instead of Neo4j
- **`suijin/core/constants.py`** — centralized magic strings: model IDs, default ports (5906/8080/55553), scoring thresholds (5/6/7/8/9), timeouts, limits, deception params, blue team file paths, configurable TMP_DIR
- **`suijin/tools/guardrails.py`** — extracted from dispatch.py: 14 blocked command patterns, `is_dangerous()`, `confirm_global_action()`
- **`suijin/tools/workspace.py`** — extracted from dispatch.py: `resolve_workspace_path()` with symlink resolution, allowlist boundary checks
- **`suijin/tests/test_tools.py`** — 43 behavioral tests: all 14 blocked patterns, edge cases (case insensitivity, whitespace), workspace security (symlink bypass, allowlist, traversal), constants validation (threshold ordering, TMP_DIR env var)
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
- Hardcoded `SUIJIN-ADMIN-2026` admin key — replaced with env var + random fallback
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
