# Suijin — Project Context

> The architecture grip. Read top to bottom before touching anything.
> Last updated 2026-10-24 (v7.3.0 The Anywhere Release: box engine parity orb+docker, adopt-a-container, box shell, clean build rows, PATH everywhere, filtered-network CAs; failover attribution + backoff; session teardown; no-hardcode sweep + history scrub; workspace-first KG.)

## Repo facts

- **Agent memory vault: `agent-vault/` (gitignored) — read it FIRST on a fresh session.** Decisions, session logs, engagement memory, roadmap state. Pointers from `AGENTS.md`.
- Repo: `~/suijin` (live tree), GitHub: `https://github.com/0xwi11iam/Suijin`
- venv: `.venv/bin/python` (Python 3.14); CI matrix py3.10/3.11/3.12
- Gates: `.venv/bin/python -m pytest suijin/tests -q -m "not ai and not slow"` (~2918 passed) + `-m slow` + `ruff check` + `ruff format`. **The suite is HERMETIC**: `tests/conftest.py` redirects `HOME`, `SUIJIN_WORKSPACE`, `SUIJIN_CONFIG` and `SUIJIN_ENV` into a session sandbox AT IMPORT TIME (before any `suijin` import — `Path.home()` is baked into `PACK_ROOTS`/`WORKSPACE_DIR`/`CONFIG_PATH` on first import, so redirecting later does nothing). A test that needs the real home must opt in explicitly. KNOWN IN-FULL-RUN FLAKE (pre-existing, P0 tree consolidation): the `sys.modules` bridge (`suijin/modules/*` ↔ `suijin/server/*`) makes ~41 tests fail + 19 collection errors in one process depending on order; every affected suite is green run in isolation.
- Version 7.2.0 published (PyPI/GHCR/Release); `suijin/version.json` was stale at 7.1.0 — bumped (it is the single source of truth for `suijin --version`).
- Operator runs via `~/.local/bin/suijin` → `~/.suijin/venv/bin/python` + `~/.suijin/repo` (symlink to this tree). Their config: `suijin/config.json` IN THE TREE (provider: opencode / deepseek-v4.1-flash-free). `~/.local/bin/suijind` is the detached-daemon launcher.
- System-Python stale processes squat lab ports (5906/5910/5911) — check `lsof -nP -i :PORT`.
- `gh` CLI not authed; use `git credential fill`.
- **Kali machine**: the operator's `kali` OrbStack machine has the pipx install at `/opt/pipx/venvs/suijin` + lab at ports 6000-6020. Refresh: `cd /opt/suijin && sudo git pull && sudo rm -rf build *.egg-info && pipx install --force /opt/suijin`. The `build/` + `*.egg-info` MUST be purged before reinstall or deleted files resurrect (field-verified).
- **Backup rule**: `backup-suijin` after EVERY substantive round — mirrors repo + `~/.suijin` to the "Backup" partition with `.prev` generation rotation. See `agent-vault/decisions.md`.
- **PERMANENT container rule**: NEVER `~`/`$HOME` in containers (OrbStack binds the Mac home — the wipe cause). Container runs use SUIJIN_WORKSPACE=/opt/... only.

## THE INVARIANTS — breaking any of these broke the field

### Target landscape & breadth (2026-10-18)
0. **The agent sees PRODUCT FAMILIES, not just endpoints** — `landscape.py` detects families (a large platform → 10) and renders "X/Y explored" in think context. The BREADTH ALERT fires when >60% of last 20 actions target one family while untested families exist. The depth-over-coverage gate is FAMILY-SCOPED — surface_expand is blocked only for the same family as the pending chain, never across families (the blanket block caused heavy tunnel vision in a live run).
0a. **Contamination scrub on .sje resume** — dead credential literals (verified SERVICE_DISABLED in a prior run) are scrubbed to `[SCRUBBED:dead-key]` before state seeding. K1/K2 API keys from dead runs stop appearing as fresh discoveries.
0b. **Temp dir auto-resolution** — bare `/tmp/` in execute_terminal rewrites to engagement `tmp/` in-place. `/var/tmp/` preserved (sandbox denial is load-bearing).

### Config & enforcement
1. **Config precedence**: `suijin/config.json` file value WINS over every code default (`load_config` setdefaults only; Pydantic `model_dump()` fills holes). Changing a model default does NOTHING if the operator's file pins the key — check their file. Menu launches RELOAD config at launch time (the stale-banner fix).
2. **Cost enforcement lives in THREE places**: `governor.py budget_guard` (`max_cost_usd`, absent→$25 hard stop), `supervisor.py` L327+ (`cost_budget_usd`/`cost_hard_cap_usd` steering), `supervisor.py` L216 (`cost_alert_usd` flag). **0 = DISABLED at all three (via `> 0` guards — `cost >= 0` is always true, the trap)**.
3. `max_iterations` is HARD-ENFORCED (2026-10-17): the graph wrapper ends cleanly at N of N with `budget_exhausted`. The recursion limit is `N*2 + 4` headroom. Bookkeeping turns can't crawl past the cap.

### Tool contracts & agent UX (2026-10-18)
3a. **Tool contracts** (`tool_contracts/`): 17 top tools have JSON schemas + usage docs. Dispatch validates args BEFORE routing — the model gets "Argument 'compare' must be an object/dict" at the boundary, not `'bool' object has no attribute 'get'` three layers deep. Usage docs ride the tool catalog in the think context.
3b. **Observation engine** (`observation/`): http_request + http_replay record (endpoint, credential, params, status, size) per call. Deterministic IDOR/BFLA/mass-assignment detectors surface findings from accumulated evidence — no LLM needed to see that user_a and user_b got different responses.
3c. **Vuln-class sub-agents** (`vuln_agents/`): 9 specialist profiles. deploy_subagent detects the class from task text and injects the specialist's doctrine + tools.

### Provider layer (`server/providers/lib/__init__.py`)
4. `generate()` returns STRINGS, never raises. `"Error:"` prefix = the failover protocol (`generate_with_failover` falls through only on it).
5. `on_delta(kind, piece)` callback errors stay swallowed; `_stream_chat` returns `(status, content, reasoning, usage, body)`, status 0 = transport death → ONE `_post_chat` fallback.
6. zai: 403 = endpoint mismatch, NO retry; 402 = plan quota/credits, explain the ~5h reset. 401/402/403 all emit `_diag_llm_done`.
7. `llm_client.generate_async`: 180s hard timeout → error string. Silent (ONE Live region rule — the strip).

### Agent graph (`server/agent/lib/`)
8. **Guidance = file-based** (`live_guidance.md` in engagement_dir), consumed once per think turn, delivered as the LAST USER MESSAGE. Two writers (input box, pause console), one file. Never `update_state` guidance.
9. **Tools dispatch via 3-arg `route_tool(name, args, config)`** everywhere; blue swaps the whole function (`route_blue_tool`), never the signature.
10. **Router keys on `_current_step.tool_name` truthiness** — bookkeeping actions (complete/ask_user/switch_skill/transition_phase) MUST clear `_current_step` or the last tool re-executes. `deploy_subagent` uses `""`.
11. `completion_reason` truthy = the ONLY normal graph exit. `.sje` resume must scrub it to None. **Completion refusals are decisions** (not no-progress); after 5 refusals of the same gate, the gate stands down.
12. Circuit breaker: 3 consecutive provider/parse failures → forced end. Redteamer grants ONE full restart for provider_failure/llm_error only.
13. Budget guard runs BEFORE every LM call in `_think` wrapper. messages/execution_trace hard cap 25 in `_merge_state`.
14. **Blue mode swaps THREE things together**: prompt builder, tool router, subagent tool reference.
15. failure-prefix sets: dispatch `_FAILURE_PREFIXES` == execute_tool_node's success check — keep synchronized.

### Chain & loop enforcement (2026-10-17/18)
15a. **Chain heartbeat**: `_on_confirmed` → `on_finding_confirmed()` → module-level `_PENDING_HANDOFF` → think_node drains into state → CHAIN directive renders → escalates to CHAIN REQUIRED at 2+ turns stale → completion refused while unexplored. The old throwaway-dict bug meant the directive NEVER rendered in 202 iterations.
15b. **Loop detection layers**: Layer 1 = within-turn dedup (dispatch). Layer 2 = cross-turn RepeatDetector (same tool+args, nudge at 3, abort at 5). Layer 3 = meaningful-progress signals (findings/surfaces/creds = progress; gate refusals = dialogue; empty steps ≠ tool success).
15c. **Blackboard**: `_on_confirmed` → `propagate_confirmed()` → mesh system announce → peer think context renders "PEER FINDINGS" section. Confirmed findings reach peers without the model choosing to broadcast.

### Weaponization — impact over volume (2026-10-20)
15d. **One source of truth**: `agent/lib/weaponization.py` — CLASS_WEIGHTS (all 15 coverage classes, cmdi/ssti=15 → info=1), BLOCKING_MIN_WEIGHT=5, satiation thresholds (4 trivia + 0 real), scenario pattern table, WEAPONS block. Coverage gate, scoreboard, think sections, and the recon-tool nudge ALL read it.
15e. **Weight-aware gate**: `untested()` sorts weight DESC (crown jewels lead); `completion_blocked()` counts only weight≥5 cells — info/redirect never block. The agent can no longer buy completion with trivia cells.
15f. **Satiation is pressure-only**: DEPTH DIRECTIVE leads DRIVE while saturated (+ PROMOTE order if still recon); recon tools (surface_expand/crawl/api_disdiscover/js_bundle_analyze/source_map_probe/dom_recon/smart_fetch) get the warning PREPENDED to their output. Never refused, never forced.
15g. **ATTACK_SCENARIOS + WEAPONS sections** render in exploitation/post_exploitation phase contexts (skipped when empty); DEPTH OVER BREADTH doctrine lives in `prompts/base.py` adversarial core; completion_status carries an impact score + saturation warning.

### Authorization & mesh scoping (2026-10-20, round 3 — the decline post-mortem)
15r. **The mesh is ENGAGEMENT-SCOPED**: every node carries its engagement slug (captured at `start()`, fixed for the node's lifetime); gc.log/DM lines carry it as a 5th field; `poll_chat()` renders ONLY same-engagement lines (untagged legacy lines hidden from tagged nodes; untagged nodes see everything — legacy behavior). Field origin: a run read DEAD peers' exploit broadcasts from a *different* engagement (18h stale) in the shared gc.log tail and reasoned the operation covered two unrelated targets — authorization-refused on the strength of it. `publish_state` digests carry the slug too.
15s. **Authorization is machine-backed** (`agent/lib/authorization.py`): engagement boot writes the operator's authorization-of-record per host to `WORKSPACE/authorizations/<host>.md` (the inspectable contract — prose to re-litigate is how the decline spiral started); RULES cites it every turn; `detect_affirmation()` on operator guidance sets state `_auth_final` (persisted) + delivers the OPERATOR RULING (final) message.
15t. **The authorization gate**: DECLINE-type completion reasons (`is_decline_reason`) are refused in the cascade — with `_auth_final` the refusal says the operator HAS affirmed (closed); without, it points at the artifact and routes concerns to ask_user. Joins the 5-refusal stand-down contract. THE OPERATOR OWNS AUTHORIZATION; the model never unilaterally ends an engagement over it.
15u. **end_audit runs in the finally** — every ending (decline, /quit, crash) stamps the trail `ended`, exports markdown, writes BBP submissions. Field forensics: a declined run's unwind never reached the normal path's end_audit — trail `ended: None`, no final report, no session.complete event.
15v. **Diag writes to `<home|ws>/logs/diag.log`** (single segment) — the old `~/.suijin/workspace/logs/logs` default resurrected the retired workspace tree after every cleanup (two husks accumulated in one day).
15w. **BBP reports land INSIDE the engagement** — `bug_bounty_report.py` used `.parent / "reports"` (one directory too high, littering the engagements root). The coverage ledger `_save` drops writes resolving to `_default` (marks without a live engagement go nowhere).
15x. **Credentials export as env vars (2026-10-27)** — every stored credential is exported as `$SUIJIN_CRED_<SANITIZED_SERVICE>` into every `execute_terminal` command (`terminal.py` merges `credential_store.cred_env()` into `executor_env()`); `creds_add` names the var, `creds_list` masks values (first-3 + length, never the literal), browser captures store SERVER-SIDE at capture time so the literal never enters model-visible text. Tests: `suijin/tests/tools/test_credential_env.py`.

### Gate machinery hardening + anti-gaming (2026-10-20, round 2)
15h. **GATE STATE PERSISTS** (F1): `_chain_pending`, `_footholds`, `_hypotheses`, `_completion_refusals`, `_chain_explored` ride REPLAY_KEYS + RESUME_SNAPSHOT_KEYS, and think_node EMITS them every turn (the snapshot filter drops keys the node output doesn't carry — adding to the tuples alone is NOT enough). Before: every resume silently dropped a pending chain and reset all refusal counters.
15i. **Chain explored matcher reads `tool_args`** (F2): the trace stores `tool_args`; the old `args`-only read meant actions could never clear a chain — only thoughts.
15j. **Confirm-time chain hypotheses ride the module handoff** (F3): `add_chain_hypotheses` at confirm time wrote to a throwaway dict and died; they now merge into graph state at drain.
15k. **completion_status is LIVE** (F4): the tool's `agent_state` service was dead (kernel-Context registration referencing a deleted `AgentState` symbol). The redteamer now maintains `_LIVE_AGENT_STATE` (in-place merge from the astream loop) registered on the TOOLS services seam — the agent can finally check gates BEFORE completing.
15l. **Hypotheses machine-clear** (F5): `auto_clear_from_trace()` runs each think turn — a successful probe-class tool call matching the hypothesis target, or a tested coverage cell for its class, clears it. Before, `mark_tested` had NO caller: the hypothesis gate could only end via the 5-refusal stand-down.
15m. **THE COVERAGE TOOL VOCABULARY BUG** (field-critical): the tool contract's action enum (tested_vulnerable etc.) disagreed with coverage.py's verbs (mark/note/untested/gate) — `action=mark` failed contract validation, `action=not_applicable` silently fell to summary. THE AGENT COULD NEVER MARK A CELL: the coverage gate could refuse but never be satisfied; the stand-down was the only exit. Both vocabularies now work (status-as-action implies mark); the schema enum carries both sets.
15n. **Anti-gaming red flags** (A): `weaponization.red_flags()` — bulk copy-paste evidence (≥5 identical), too-fast (≥10 marks, <5 probe calls), N/A flood (>60% of ≥8 marks). They block completion via the INTEGRITY GATE and render in the scoreboard. `not_applicable` on weight≥5 cells requires ≥50-char justification; trivia cells mark freely.
15o. **Chain candidates are obligations** (B): detected finding pairs (pattern table with clearing CLASSES) block completion via the chain-candidate gate until a tested_* mark lands on one of the pattern's classes or an explicit `_chain_explored` mark. Rendered as ⛓ OBLIGATION lines in ATTACK_SCENARIOS. Shared 5-refusal stand-down is the deadlock backstop.
15p. **Severity honesty** → **RETIRED by SEVERITY-AI ruling (2026-11-20, commit 0d34465a)**: the operator ruled the AI defines severity — `_honesty_check()` no longer caps, `system_severity()` is never stamped, `_write_finding` renders the stated severity verbatim, and `classed_title` shows the stated word with CVSS riding along (supersedes the 2026-09-17 CVSS-display rule). Both functions remain defined for import compat but are dead on every write/display path.
15q. **Stop-condition doctrine** (D): DEPTH OVER BREADTH carries the explicit stop conditions (untested high-impact cells / red flags / unexplored chains) + the "when you think you're done" ladder (param pollution, method switching, chained attacks, edge cases, races) + "check completion_status BEFORE deciding to complete".

### Operator console (`modules/console/lib/`)
16. **ONE Live region** (the strip, transient). Everything else prints above it.
17. **Stdin has ONE owner**: TTY → RedInputReader (cbreak, ISIG on). Never `console.input()` while a cbreak reader lives. ask flow → `begin_ask` queue; ESC ESC → pause runs IN THE READER THREAD.
18. All 19 EngagementUI methods are `_guarded` — never raise into the loop.
19. Enter PRESERVES the input buffer; arrows never reach it; the armed pause queue survives `end_pause`.
19a. **Mesh phase lines deduped** — `set_phase(silent=True)` when think_node already announced (the doubled-line bug). The input badge syncs on every iteration header.
19b. **Detail pane sanitizes** raw ANSI/control bytes from tool output (the real-engagement garbling); long tokens fold at the border. Iteration upserts prevent blank stub rows.
19c. **t/s gauge** updates per streaming delta (via `_emit` recording arrivals); the engagement republishes the runtime digest every 0.5s.

### Termination & errors
20. EVERY ending hits `_render_termination` (classifier: DECLINED/OPERATOR/NO_OUTPUT/FAILED/COMPLETE/BUDGET).
21. The queue-bridge astream reader CAPTURES exceptions into `_stream_error` and the sentinel handler re-raises.
22. `run_red_team` NEVER exits silently: crash panel + `outputs/logs/engage_crash.log`. **Reports generate on EVERY ending** (the finally generates final_report.md from the trail — idempotent).

### Kernel & boot
23. **Kernel purity**: kernel imports stdlib + suijin.kernel ONLY (AST-tested).
24. `register()` is cheap/no-I/O; `start()` goes live. Core-tier failure aborts; others degrade silently.
25. **Services are lazy**. TWO registries coexist: kernel Context + `tools/lib/services` seam — register on the right one.
26. Console is feature-blind: modules register menus/verbs via `console_hooks`.
27. New CLI verb → ALSO add to `_KNOWN_VERBS` (cli.py).
28. main.py sys.path bootstrap runs BEFORE any `from suijin import …`.
29. **v7.2 CLEAN LAYOUT**: `~/.suijin/config/` IS the workspace (WORKSPACE_DIR) — profiles/skills/packs/SOUL.md/prompt.md/reflex-shadow/drive_policy/approvals/credentials/notify/recipes. Engagements at `~/.suijin/engagements/<stamp>_<slug>/`. System dirs `~/.suijin/logs/` and `~/.suijin/caches/`. Pre-engagement writes park at `config/artifacts/<name>/`. The old `~/.suijin/workspace` is retired (one-time migration). The repo `suijin_agent` symlink is REMOVED. `outputs/` is GONE.
29a. **Run logs go through `workspace.logs_dir()` = `~/.suijin/logs/`**.
29b. **A crash is still an engagement**: .sje through `CRASH_SAVER`; the panel prints the resume line.
29c. **Husk prune**: no events.jsonl + no audit_trails + no .sje + empty log/ = never had a run → removed.
29d. **Reports read the audit trail, never model JSON**: one overwriting `reports/report.md` mid-run; `reports/final_report.md` at conclusion via `end_audit`.
29e. **PERMANENT container rule**: NEVER `~`/`$HOME` in containers. Container runs set SUIJIN_WORKSPACE to explicit container-local paths.
29f. **mesh honors SUIJIN_WORKSPACE containment** — the mesh lives INSIDE an explicit workspace override (`<ws>/mesh`), never scattered to the parent. The operator's MESH_DIR/WORKSPACE_DIR constants derive from the same rule.

### Provider layer (`server/providers/lib/__init__.py`)
4. `generate()` returns STRINGS, never raises. `"Error:"` prefix = the failover protocol (`generate_with_failover` falls through only on it).
5. `on_delta(kind, piece)` callback errors stay swallowed; `_stream_chat` returns `(status, content, reasoning, usage, body)`, status 0 = transport death → ONE `_post_chat` fallback.
6. zai: 403 = endpoint mismatch, NO retry; 402 = plan quota/credits, explain the ~5h reset. 401/402/403 all emit `_diag_llm_done`.
7. `llm_client.generate_async`: 180s hard timeout → error string. Silent (ONE Live region rule — the strip).

### Agent graph (`server/agent/lib/`)
8. **Guidance = file-based** (`live_guidance.md` in engagement_dir), consumed once per think turn, delivered as the LAST USER MESSAGE. Two writers (input box, pause console), one file. Never `update_state` guidance.
9. **Tools dispatch via 3-arg `route_tool(name, args, config)`** everywhere; blue swaps the whole function (`route_blue_tool`), never the signature.
10. **Router keys on `_current_step.tool_name` truthiness** — bookkeeping actions (complete/ask_user/switch_skill/transition_phase) MUST clear `_current_step` or the last tool re-executes. `deploy_subagent` uses `""`.
11. `completion_reason` truthy = the ONLY normal graph exit. `.sje` resume must scrub it to None.
12. Circuit breaker: 3 consecutive provider/parse failures → forced end. Redteamer grants ONE full restart for provider_failure/llm_error only (NOT provider_out_of_credits).
13. Budget guard runs BEFORE every LM call in `_think` wrapper. messages/execution_trace hard cap 25 in `_merge_state`; execute_tool's trace return REPLACES think's opened step (iteration-keyed).
14. **Blue mode swaps THREE things together**: prompt builder (think `_blue_mode`), tool router (`route_blue_tool`), subagent tool reference (module-attr detection). A blue router with a red tool list = the BF2 bug.
15. failure-prefix sets: dispatch `_FAILURE_PREFIXES` == execute_tool_node's success check — keep synchronized.

### Red TUI (`server/redteam/lib/red/`)
16. **ONE Live region** (the strip, transient). Everything else prints above it. Stop the strip before printing prompts/panels that must survive; restart after. The strip repaint clobbers bare prints.
17. **Stdin has ONE owner**: TTY → RedInputReader (cbreak, ISIG on); RunBox line reader OFF. Never `console.input()` while a cbreak reader lives (the hang bug). ask flow → `begin_ask` queue; ESC ESC → pause runs IN THE READER THREAD.
18. All 19 EngagementUI methods are `_guarded` — never raise into the loop. UI_STATE keys are cross-module API (`/cost` reads `last_ttft`) — don't rename.
19. Enter PRESERVES the input buffer; arrows never reach it; the armed pause queue survives `end_pause`.

### Termination & errors
20. EVERY ending hits `_render_termination` (classifier: DECLINED/OPERATOR/NO_OUTPUT/FAILED/COMPLETE). provider_out_of_credits gets the loud red banner + press-Enter. Crashes: ui.stop() FIRST, then the panel (bare lines were wiped), reader stopped before `input()`.
21. The queue-bridge astream reader CAPTURES exceptions into `_stream_error` and the sentinel handler re-raises — KI/CancelledError pass through (pause path).
22. `run_red_team` NEVER exits silently: crash panel + `outputs/logs/engage_crash.log` (appended, survives).

### Kernel & boot
23. **Kernel purity**: kernel imports stdlib + suijin.kernel ONLY (AST-tested). diag.py resolves workspace from env, never modules.
24. `register()` is cheap/no-I/O; `start()` goes live. Controller registers ALL before starting ANY. Core-tier failure aborts; others degrade silently (quiet-boot).
25. **Services are lazy** (zero-arg producer, materialize on first get, None when missing). TWO registries coexist: kernel Context (gateway boots) + `tools/lib/services` seam (TUI path, registered in `init_runtime`) — register on the right one.
26. Console is feature-blind: modules register menus/verbs via `console_hooks`; must tolerate `hooks is None` (headless). `unregister_owner` is the only bulk removal.
27. New CLI verb → ALSO add to `_KNOWN_VERBS` (cli.py) or `python main.py <verb>` / container dispatch breaks (pytest argv firewall).
28. main.py sys.path bootstrap runs BEFORE any `from suijin import …` (container fix).
29. **v7.2 CLEAN LAYOUT (2026-10-12)**: `~/.suijin/config/` IS the workspace (WORKSPACE_DIR) — profiles/skills/packs/bugscope/SOUL.md/prompt.md/reflex-shadow/drive_policy/approvals/credentials/notify/recipes live under it. Engagements at `~/.suijin/engagements/<stamp>_<slug>/` (lazy subdirs; log/+state/ at create). System dirs `~/.suijin/logs/` (logs_dir()) and `~/.suijin/caches/` (models.dev, ms-playwright). Pre-engagement artifact writes park at `config/artifacts/<name>/` — idle reads never conjure `_default`. NOTHING else at the root. The old `~/.suijin/workspace` is retired (one-time `_migrate_legacy_workspace` folds survivors, parks husk at workspace.retired-*); the repo `suijin_agent` symlink is REMOVED, never recreated. `outputs/` is GONE: tool dumps → engagement `toollogs/`, surface verdicts → `state/`, pack exports → `config/packs/`. (Briefly rolled back same-day by mistake — RESTORED; the rollback round's backup had overwritten the only mirror, the rebuild came from the session record. The backup script now keeps one `.prev` generation.)
29a. **Run logs go through `workspace.logs_dir()` = `~/.suijin/logs/`** — same path the panels advertise. Never `WORKSPACE_DIR/"logs"` or `"outputs"`.
29b. **A crash is still an engagement**: the exception path saves a `.sje` through `CRASH_SAVER` (once-flagged, never raises) and the panel prints the resume line. The loop-breaker/`error:*` endings save via the normal end path and return to the menu.
29c. **Husk prune**: an engagement with no events.jsonl, no audit_trails, no .sje and an empty log/ never had a run — the redteamer finally removes it (the paired-folder litter from aborted launches).
29d. **Reports read the audit trail, never model JSON**: `generate_report` (tool) pulls `current_trail()` via `report_inputs()` (the ONE remap); one overwriting `reports/report.md` mid-run, `reports/final_report.md` written automatically by `end_audit` at conclusion. No timestamped copies.
29e. **PERMANENT container rule**: NEVER use `~`/`$HOME` in containers — OrbStack binds the macOS home into them (the wipe cause). Container runs set `SUIJIN_WORKSPACE` to an explicit container-local path.
30. Audit/diag/journal/events NEVER break the run (never raise; args digest-only; re-entrancy bounded).
31. Tool calls are data: `call_tool` returns `"Error: …"` strings, every call audited, tools namespaced.
32. Resume precedence: **engagement STATE rides the .sje; operator SETTINGS ride live config.json (current wins)** — the deepseek-402 incident.


## Architecture in one paragraph

`~/.local/bin/suijin` → `modules/console/lib/cli.py` (argparse, ~56 verbs) → no-argv → `suijin/main.py` mode selector, which opens **straight into the menu with no keypress gate** (the old "Press Enter to continue" was pure friction; banner + missing-tools notice are information, not a screen to dismiss) → Red Team → objective → `run_red_team_async` (redteamer.py), which is the FULL in-process Rich TUI: builds `SuijinAgentGraph(generate_fn=_generate_with_stream, route_tool_fn, max_iterations, run_config)` with nodes initialize→think→execute_tool→generate_response (NO supervisor node; supervision inline in `_think` wrapper: governor, circuit breaker, supervisor/oracle/drift cadences). Drives via queue-bridge astream; think reads `live_guidance.md` as last user message; tools go through `route_tool` 3-arg (repeat-guard, healing, mode gates); execute auto-backgrounds >10s into `job_registry` (daemon threads, drain once); fireteam subagents (max 5, semaphore 3, on_delta=False) drain at think start. Termination: completion_reason vocabulary {Objective-complete free text, budget_exhausted, llm_error, provider_out_of_credits, provider_failure, parse_failure, node_crash, error:*, None-on-resume} → classifier → banner. Session end TEARS DOWN background machinery (`job_registry.cancel_all` + `mesh.stop` beside the librarian stop): daemon threads used to outlive the session — the mesh heartbeat kept beating and tool jobs kept scanning ten minutes after provider_failure, and their prints interleaved with the mode-selector redraw (the mangled-banner field report 2026-10-23). Providers: `generate()` string-returning dispatch (zai streaming SSE, coding/paas endpoints), `generate_with_failover` chain, `USAGE` tracking with priced flag. Workspace `~/.suijin/workspace` holds everything; .sje bundles = engagement state + (stale) config snapshot, resume merges current config over it.

**The drive controller (2026-09-28, four stages):** the system around the frozen LLM became a second agent — `agent/lib/epistemic.py` (belief state: known/suspected/open-questions, surprise detection, leak→hypothesis generation), `agent/lib/drive.py` (interest/ambition/boredom/urgency dynamics; read-only REFLEX probes on unresolved surprise; ±0.2 temperature coupling; a one-shot continuation veto; the earned SELF-MODEL identity), `agent/lib/drive_bandit.py` (UCB1 learner, `drive_policy.json`, only-CONFIRMED-pays). Config: `run_config.drive.{enabled,reflex,continuation}`. Bench reports a `drive` scoreboard. Cross-session: UNFINISHED BUSINESS flushes to scratchpad.

**The agent's judgement machinery (2026-09-27):** results enter state DISTILLED (8k cap, secrets excepted); findings/creds/operator answers ride as `[PIN]` messages that compaction carries verbatim; the FOOTHOLD GRAPH makes chaining a state machine (CONFIRMED → YOU HOLD section every turn → depth gate refuses completion while unlocks are untested); the supervisor is a COACH (facts_brief + cite-a-fact-to-speak + a heeded/escalate/drop feedback log, silent by default, 1 message/3 turns); and terminal windows MESH via the filesystem (`agent/lib/mesh.py`: registry+GC+DMs+digests, ephemeral, untrusted-wrapped — see `agent-vault`, gitignored).

**Who runs what.** `suijin` is the PERSON: one process, in-process Rich TUI, everything automatic under the hood (journal, crash-safe `.sje`, resume). `suijind` / `suijin daemon|ps|attach|stop` is the DEVELOPER shipping mods and long detached runs — a second process that talks **files, not a protocol** (`events.jsonl` out, `live_guidance.md` in, SIGTERM to stop; `attach_daemon` polls at 0.2s). `suijin gateway` is an opt-in HTTP/WS server (`127.0.0.1:7331`, FastAPI + `/events`) that boots its own kernel via `controller.boot()` and currently has **no in-tree client** — the intended consumer is the desktop app. So the client/server shape exists in code, but the default path is deliberately NOT split; do not read the `suijin/server/` directory name as a running server.

## Session history (what's done)

- v5.6.0→v5.7.0 published; BF4-BF7 (cases/dossiers, retention/hunt, metrics, learning), deathmatch scenarios, catalog_exploit w/ severity, .sje bundles, PTY rig, diag logging, provider streaming, guidance-as-last-message, workspace restructure — all committed.
- 2026-08-30 session: infinite iterations (BOTH model default 100000 AND operator file 100000), cost enforcement 0=disabled at all three sites (governor `or`-swallow fixed, supervisor `>0` guards), cursor-left input box, crash visibility (reader re-raise + red panel + press-Enter + ui.stop-first), 402 instant-kill + banner, resume config merge (current wins), provider-chain print at engagement start, `len(trace)` NameError fix (was killing all saves on early crashes), test_ai_calls marked ai+slow, pause-console/termination input() safety verified (reader stopped first).
- Fleet session (Aug 31): provider registry (24 providers — 13 cloud table rows, openrouter aggregator, 5 local keyless, custom:<name> LAN boxes via config custom_providers); _compat_call = the ONE generic OpenAI-compatible engine; M-token display (_fmt_tok 2.40M); bypass_403 battery tool (~24 variants through http_request pacing); code_harness dev loop (sandbox per attempt, python auto-pip triage, VERDICT PASS = finding evidence). Commits: 1efdeb4, d66087f.
- Registry invariants: adding a provider = a ProviderSpec row, never code; local specs are never priced (governor can't stop on free); cloud rows activate on key presence in .env; partial-config provider rescue (generate + generate_async merge real config under provider-less dicts — the deepseek-402 root cause).
- Web Evidence Engine (v6.6.0, waves a9fdb16→12e01fc): http_replay (payloads as DATA — 15 ops, 12 codecs incl tab WAF-evasion, compare=3-gate diff in one call, credential swap = IDOR primitive, raw-byte smuggling, module budget+AIMD) · inject_probe (battery+facts, never an oracle: sink contexts, noise floors, WAF-block qualification) · web_session (auto cross-credential model: IDOR worklist + hiddenParams) · THE COMPLETION GATE (complete refused while untried surfaces/coverage cells remain — think_node) · coverage ledger (evidence-required marking, wide notes once) · surface_expand + SURFACE STALL · suijin capability CI gate (no-orphan-code; testssl/wafw00f fixed). Hermetic-test rule: engagement-scoped stores need pinning in tests.
- Weaponizer invariants (cc6ec66): escalation is PROPOSE-ONLY (CONFIRMED finding → ready deploy_subagent task in context, model decides); foothold predicate (uid=/creds/shell-class CONFIRMED) FORCES exploitation→post_exploit with doctrine swap; what_worked = the only positive-memory reader (CONFIRMED by target + class transfer); memory keyed by host[:port] via target_key(), never objective prose; payload_mutate is analysis-only; bench failures → learnings.md → GYM NOTES in think context.
- Self-service invariants (928c0bc): adjust_config allowlist ONLY (posture/temp/tokens/provider/fallback/models) — cost caps, stealth, safety modes, proxy are structurally operator-only; set_live_config stores the engagement dict BY REFERENCE (both-seam mutation: live dict + config.json disk); provider auto-chain (llm_client._auto_chain) = first key-bearing cloud + ollama, bounded to 2; write_tool lands in the FIRST non-vendored PACK_ROOT (v7.2: WORKSPACE/modules — always where the loader scans, never the vendored tree; legacy ~/.suijin/modules stays a read fallback); missing-binary tool errors append install_hint. Latent-bug fixes: adversary_profile now reaches TUI runs, supervisor_interval is config-driven.
- ENVIRONMENT CONTRACT (2026-10-20): the base doctrine carries an explicit machine contract (not-a-container, `$HOME`/`$TMPDIR` = engagement home/tmp, writes-jailed-reads-and-network-free, /tmp auto-rewrite + /var/tmp denied-by-design + shared-/tmp warning, background=true for long jobs) — the model previously learned the three jail layers by bumping into them, 3 iterations at a time. The shared-/tmp warning uses the `(?<!var)/tmp` guard so a denied /var/tmp probe no longer ALSO gets a contradictory "shared /tmp" warning.
- REFERENCE-ADOPTION BATCH (2026-10-20 round 5): **url_dedup/url_rewrite/url_merge** (hexstrike uro/qsreplace/anew — template-collapse URL floods before they hit context), **csrf_extract** (fresh token → ready http_replay mutation; the stateful-request closer), **ssrf_listener** (OOB evidence: start/hits/stop; every hit logged to engagement state as the finding's proof), **ensure_tools** (idempotent toolchain self-provisioning, max 3 installs/call), **tool_search** + catalog budget line (lazy-registry visibility: 89 indexed, keyword search with usage docs), **tool-substitution fallbacks** (missing/failed tool → equivalent-coverage alternates inline: nmap→rustscan/masscan, gobuster→ffuf...), **guardrail block psychology** (ARTEX: denials state PLATFORM CONTROL, not-target-defense — the model stops WAF-bypassing its own guardrails), **repeat-echo** (pentagi: the loop nudge carries the tool's previous answer — the suppressed answer was the diagnosis once), **tamper-evident evidence store** (ARTEX: CONFIRMED → sha256 content-addressed blobs + self-hashed manifest; the report re-verifies and renders DRIFT warnings).

### the box — sealed linux environment (2026-10-20 round 7)
BOX. **`suijin box <verb>`** (alias `container`; no verb = the curses manager tui): build · start · stop · enter · shell · adopt · unadopt · status · refresh · destroy. **ADOPT (2026-10-24)**: `suijin box adopt <name>` points suijin at a container the OPERATOR already runs (config `container.name`; every engine call routes through `box_name()`) — provisions into it with the standard stages, and since adopted containers get no live mount, the source tree tar-streams to `/opt/suijin` (agent-vault/.venv/.git excluded — secrets never cross) and pipx installs from there. **Destroy NEVER deletes an adopted container**: it removes only suijin's footprint inside (`/opt/suijin-ws`, `/opt/suijin`, the profile dropin — explicit /opt paths) and says so; the typed confirm reads `clean <name>`. `unadopt` reverts to the managed box. Field-proven on a throwaway docker container: adopt → base+stream+3 tools in 92s, destroy cleaned but `docker ps` still showed it running. Engine layer: **orbstack on macOS, docker everywhere else** — full compatibility for both (2026-10-24 ruling). Order: `SUIJIN_CONTAINER_ENGINE` env > `container.engine` config > one-time interactive pick (ONLY mac + both runtimes + TTY; persisted to config; config consulted BEFORE any prompt) > autodetect (mac: orb first; linux/windows: docker — a Linux user is NEVER asked, and an `orb` binary off-mac is ignored: `_orb_available` gates on darwin). DOCKER PATH: same resource posture minus the disk cap (`--memory 4g --cpus 2`; `--storage-opt size=` only works on pquota-xfs and would fail creation elsewhere), the repo mount is CONDITIONAL (pip-installed hosts have no tree; `-v ""` would kill `docker run`) with Windows path translation, `docker exec -i` for stdin tar-streams via `box_cmd_argv(stdin_attached=True)`, and the kalilinux image runs as ROOT WITHOUT sudo — base bootstrap and `/opt` creation use `$S` (sudo only when present), `sudo` is a base package so every manifest sudo-line works on root. ORB NOTES (field-proven): `orb create` takes a DISTRO name (`kali`), not a docker ref; `orb list` shows running machines only — the existence probe is `orb -m suijin-box true` (auto-starts stopped machines); the default user is unprivileged with passwordless sudo. **Clean rows** (2026-10-24): installs run as `apt-get -q -o Dpkg::Use-Pty=0` (`_box_line` rewrites the manifest's friendly `apt install`) and the stream pump collapses `\r`-redraws to their final state (`_clean_stream_line`) — apt's pty progress used to shred the build output into diagonal scattered rows. **Tools on PATH in EVERY shell**: `/etc/profile.d/suijin-box.sh` (login shells — every exec path) AND `~/.bashrc` (the interactive `box shell` session); verified live: aws (pip ~/.local/bin), gcloud (SDK bin), httpx (go bin), john (sbin) all resolve in login, interactive, and `box shell` sessions. `suijin box shell` = straight into bash, no drift ceremony (that's `enter`). **Build screen** (2026-10-24): stage rows recolor LIVE via `build(stage_cb=...)` — done green, doing white, not-started dim gray bullets (the tui's stage state used to never update: done stages showed white/gray forever); the current tool shows a live elapsed clock (in-flight progress events); the output pane fills the whole bottom.
BOXb. **Stages are resumable** (state in `/opt/suijin-ws/.box-state.json`, recorded per stage AND per tool): base packages → editable pipx install from the live-mounted repo (`git pull` on the host = new code in the box instantly) → shell profile drop-in (`SUIJIN_WORKSPACE=/opt/suijin-ws`, PATH carries `~/.local/bin` + `~/go/bin`) → host→box config sync → toolchain (the DECLARATIVE MANIFEST below) → first-boot verify. The tools stage RECONCILES every build (per-tool state is the source of truth; a complete box finishes in one no-op read) and the summary counts only the CURRENT `container.tools` list. **Presence is the truth — installer exit codes lie through pipes** (pipefail+presence rule), and presence is the manifest's DECLARED verify: a binary name (`testssl.sh` ships `testssl`) or a python module (`requests` has no binary). Verified live end-to-end on OrbStack (all stages green, `suijin 7.2.0` inside, apt path AND pip-fallback path both selected by the real probe, tools counted 2/2).
BOXf. **Tool manifest — `suijin/server/tools/lib/tool_manifest.py`** (2026-10-20): every tool DECLARES `platforms` (canonical vocabulary shared with pack manifests: `kali · linux · macos · windows`; host detection maps a Kali box to `kali`), `prereqs` (go/pip/gem — installed BEFORE tools, never discovered by failure), an ORDERED `install` fallback chain per platform (apt line first when it exists, declared runtime fallbacks after; every apt line `-y --no-install-recommends`), and `verify` (binary/module — some tools accept alternates, e.g. `nxc` OR `crackmapexec`). Sources: pack manifests' optional `tool_install` block (packs can declare install metadata for their own deps) > hand overrides > derivation from the doctor's `_INSTALL` hint table. The box's plan batches ONE apt probe (by PACKAGE name — `ldap-utils` ships `ldapsearch`) and it is run for EVERY tool that lacks an explicit apt line too, naming the package after the tool: Kali packages more than the hint table knew (trufflehog, mitmproxy, azure-cli were all rescued this way). The installer chain genuinely falls through line by line until declared presence passes; pip fallbacks are box-safe (`--user --break-system-packages`, PEP 668), gem fallbacks use sudo. Vendor paths exist where no distro package does: gcloud installs the official linux tarball into `$HOME/google-cloud-sdk` (PATH carries its bin), azure-cli uses the Kali package then the vendor apt script, trufflehog's Go module is unbuildable (replace directives) so it has a vendor-installer fallback, and Kali's apt dirsearch gets a `setuptools<81` user-site shim because `pkg_resources` is gone from Python 3.14. Playwright installs only the headless shell (`--only-shell`, 280MB) instead of every engine (1.3GB). `suijin tools --manifest` prints the whole contract; `suijin modules` shows pack-declared platforms/prereqs; doctor skips deps of packs whose declared platforms don't cover the host and annotates hints with declared prereqs. Drift/status uses these same declared checks (python-module deps are not "missing tools"). FIELD PROOF 2026-10-22: two clean PTY-driven OrbStack builds — 44/44 tools installed, 43/43 runtime smoke checks passed.
BOXg. **Kali rolling now ships Python 3.14** — `psutil<6.0` had no cp314 wheel and forced a C build (no compiler in the headless box). Pin relaxed to `<8.0` (7.2.2 ships an abi3 aarch64 wheel). Any future C-extension dependency must ship wheels or the base stage needs a toolchain.
BOXc. **Sync is one-way by design**: host workspace items (config.json, `.env`, packs, skills, prompts, prompt.md, SOUL.md, drive_policy, profiles) flow IN at build/refresh; `.env` is a first-class item (host workspace `.env`, SUIJIN_ENV override wins; chmod 600 in the box — provider keys must ride along or the box can't think). The box's `.sync-manifest.json` snapshot drives drift detection — `enter` compares live host vs snapshot: changed items or missing tools → fix & enter (sync + install) or just enter. Nothing the box generates ever flows back. Field-proven: touching prompt.md surfaces as drift, reverting clears it.
BOXd. **destroy archives first** (tar stream of `/opt/suijin-ws` to `~/suijin-box-archive-<date>.tar.gz`) and requires a typed confirmation (`destroy suijin-box`) — the only typed confirm in the app.
BOXh. **Box config plumbing (2026-10-23, the 5-second engagement crash)**: the box profile exports `SUIJIN_CONFIG=/opt/suijin-ws/config.json` + `SUIJIN_ENV=/opt/suijin-ws/.env`. Without them the loader fell back to the PACKAGE DIR — which in the box is the live-mounted host repo: it read the repo's keyless `.env` ("API key not set" → provider_failure ~5s after resume) and wrote the model picker's choice INTO the host tree (stray `suijin/config.json`). The `profile`/`config` stages re-run on EVERY build (`_stage_resumable` — a resumed box must pick up new plumbing and refreshed secrets, not report "already done"). `run_red_team_async` calls `load_env()` first — the TUI/daemon entries never loaded `.env`, so even a correctly-synced box died at the first provider call. Field-proven: the same box+provider that died in 5s ran 3 real iterations and paused safe.
BOXe. **tui contract (operator rulings)**: lowercase everywhere, color+bold carry meaning (green ok / yellow drift / red fail), block progress bars with ascii fallback, NO emojis, clean fit at 80 columns (compact below 60), both arrow families (the settings lesson), guarded actions — a docker/orb hiccup renders an error row, never a crash.
- 2026-09-25 hardening round: **the suite is now hermetic** (conftest redirects HOME/workspace/config/env at import; previously it loaded the operator's real packs, config and .env — the cause of the long-lived order-dependent `test_console_ui` flake). `SUIJIN_CONFIG`/`SUIJIN_ENV` override the settings/secrets paths (resolved at import, like WORKSPACE_DIR). `load_env()` had three field bugs, all fixed: a `.env` line starting with `=` raised `OSError(EINVAL)` and **crashed boot**; `export FOO=bar` created a variable literally named `export FOO` so the provider never saw the key; quotes were not stripped, so a real key became `'key'` and surfaced as an unexplained 401. **`.env` deliberately WINS over an inherited shell value** (opposite of python-dotenv) — it is the authoritative key store, so editing a key and restarting must not silently keep the old one. Four tests stopped touching the machine: no more `kill -9` on everything holding a hardcoded port, no `pkill -f vulnerable_app`, no pack written into the live `~/.suijin/modules`, no migration run against a path inside the repo. The bare `suijin` startup keypress gate is gone (menu opens immediately). Crash logs now land in `outputs/logs/` (`workspace.logs_dir()`), the directory the panels already advertised.

## Immediate next steps

1. Operator: re-run the pending engagement (config now: zai/glm-5.3, infinite, no caps). DeepSeek top-up optional (fallback only).
2. B1 (source audit — `modules/treeaudit/main.py` orphaned start).
3. Before ANY commit: full gates (`pytest -m "not ai and not slow"` ~2895, ruff check, ruff format) + never commit the operator strays.
4. **Engagement confidentiality is a PERMANENT operator ruling — see AGENTS.md.** Never name a target, program, vendor, or tested host in any committed artifact (commit messages, comments, test payloads, docs). Refer to engagements generically; tests use example.com. One commit (f547d32) predates this rule and still names a target — scrub on the next commit that touches it, history rewrite only on explicit request.

## Suijin Lab — the sophistication pass (2026-10-02, round 2)

The lab grew teeth in four directions (no scoring mechanics — the lab is
just a better target):

- CHAIN GATING (`lab/northbridge/chainstate.py`): prerequisites are now
  ENFORCED. The notify SSTI only renders for callers presenting a valid
  K4 delivery signature AND having walked a key leak; scheduler RCE
  needs the signature the notify outbox carries; the vault's k2 secret
  sits behind a half-open policy that serves only after the timing
  oracle is genuinely probed; the billing double-refund requires the
  partner coupon leaked via analytics' internal-source diagnostics.
  Spam does not fire these — reconnaissance does.
- ADAPTIVE DEFENSE (`lab/northbridge/defense.py`): per-service circuit
  breakers trip on probe spam (lockdown: everything 429s with honest
  Retry-After) and the gateway's canary family learns — repeated canary
  hits close a 404-everything tarpit on the client. Pacing and rotation
  are now tested skills.
- NEW DEEP MECHANICS, all real math/no string-matching: RS256 tokens
  with a published certificate and the classic HS256-signed-with-the-
  public-PEM confusion (l7 — textbook RSA in keys.py); a real pickle
  deserialization sink on the 2019 session cookie (legacy-api whoami);
  a GraphQL-shaped beta surface (introspection, a cross-tenant resolver
  gap, a working depth-bomb defense); and edge cache poisoning — the
  page cache keys on path while the body reflects X-Forwarded-Host, so
  one hostile header poisons the variant everyone receives.

## Suijin Lab — Northbridge (2026-10-02)

ONE lab replaced the twelve small ones (citadel, hill_ctf, aegis_vault,
oauth, wordpress, log4shell, devops_dashboard + stubs; blue_target kept
for the blue side). Northbridge is a fictional B2B SaaS built as TWENTY-ONE
real services — separate processes, ports, SQLite DBs, trust zones:

- backbone (hand-written, the three crown chains): edge gateway (rate
  limits, WAF-cloak, canaries, the internal wall), auth (JWT kids,
  minute-window resets), core-api (tenants/invoices/webhooks/exports),
  objects (buckets, presign, publish-preview), worker (queue, the SSTI
  sink), internal admin (the impersonate/payout/jobs surface)
- fifteen catalog-generated services (billing, support, wiki, analytics,
  notify, mobile-bff, status, integrations, featureflags, legacy-api,
  repo, artifacts, vault, scheduler, decoy-admin) — ~170 endpoints,
  ~26 vuln pattern classes, 40+ planted flags
- the vulnerability ladder: misconfigs → logic bugs → TOCTOU races →
  ONE key reused across four trust zones (K..K4, each leaked 2-3 ways)
  → chained SSRF/RCE/race crowns reachable ONLY through prerequisites
- volume content: 60 users, 240 articles, 300 tickets, 24 JS bundles
  (3 bury secrets) — enumeration alone is hours of honest work
- defenses ON: rate limits, validation, scanner-cloak 404s, canary
  telemetry, chain-edge events (the bench's coverage score), decoy
  flags that validate false

`suijin lab up|down|reset|status|telemetry`; `suijin bench --lab
northbridge` grades flags + chain-edge coverage (mock baseline: 2 tier-0
flags, 1 edge, decoy refused — the crowns are out of mock reach by
design). Tool suites (http_replay, inject_probe, web_session, wave4,
blue arsenal) now run against an inline deterministic target
(tests/tools/_tool_target.py) instead of any lab. Two real tool bugs the
migration exposed and fixed: compare-mode baselines ignored `credential=`
(send unauthenticated), and credential labels collided for same-alg
JWTs (24-char prefix) blinding the cross-credential worklist.

## Session mesh v2 — remote nodes over SSH (2026-10-02)

v1 (filesystem, same machine) extended: each node hosts a LOOPBACK-ONLY
JSON-lines wire server (`agent/lib/mesh_wire.py`, port in the registry);
`/connect user@host` joins a remote through ONE ssh carrying twin
forwards (`-L` to their server, `-R` back to ours). Discovery is
`suijin mesh-port` over ssh (registry scan, freshest live node). The
tunnel is the TLS; the per-host mesh key (default `root`,
`~/.suijin/keys/`, fingerprints in known_hosts.json with SSH-style
change warnings) is the auth. The key is prompted IN THE INPUT BOX and
consumed by the command — it never becomes guidance, never reaches the
model, journal, or .sje (PTY-tested). New tools: `mesh_ask` (one queued
question per remote peer, TTL-budgeted) and `mesh_reply` (by qid).
Remote peers show as `REMOTE user@host` in mesh_status and count in the
strip's ⚡N. Everything wire-delivered lands in the same runtime files
v1 reads — ephemerality and the untrusted-injection boundary inherited.

## Field fixes (2026-09-30 report, ported 2026-10-02)

Local-model support: ollama gets a 4096-token default window (no more
1M fallback silencing compaction), compaction fires at 70% with a 6-message
keep-floor for windows ≤8k, `llm_timeout` is configurable (180s default,
300s local), reasoning_effort is stripped for ollama unless the model name
declares thinking (r1/think), and fetch_authorization_page is idempotent
per target (the second call directs the agent to recon instead).


## The Dynamic Cheatsheet — cross-engagement memory (2026-10-02)

The problem (operator analysis, from the research): every memory layer
was EPISODE-scoped — the run that burned 22 JWT candidates taught
nothing to the next one. The fix is scaffolding-only persistent memory:
`agent/lib/cheatsheet.py`, a workspace-level store (capped 40, deduped,
pruned) of one-line TRANSFERABLE snippets — proven techniques and
confirmed dead batteries WITH their conditions. Two write paths: the
`cheatsheet_note` tool (agent, mid-run) and a conclusion-time
distillation (one guarded LLM call over the trace tail — catches the
"moved on" case the agent never self-reported). One read path: a
CHEATSHEET section in every think context (12 freshest, untrusted-
wrapped — snippets derive from hostile output, and memory poisoning is
a real attack). Verified LIVE: a snippet stored before a think_node run
appears inside the system message's untrusted boundary; empty store =
zero context cost. The killer demo target: the JWT battery never runs
twice.

The laya/System One plan (decision models for supervisor/oracle/coach —
10ms classifications replacing generation-priced judgment calls) is
NOTED for later; not built.

## Blue team discontinued (2026-10-02)

The entire blue-side — `server/blueteam`, `modules/blueteam`, the
blue_target lab, the treeaudit module, sparring + battle modes, the
`spar`/`eval`/`watch` CLI verbs, the blue MCP tools, blue traffic
scoring services, and the IR-1 compliance control — is REMOVED.
Blue teaming is DISCONTINUED and will RETURN AT A LATER TIME, rebuilt
on what the red side has learned since (drive controller, cheatsheet,
mesh). When it returns it should return as a peer of the red path, not
a parallel universe: same provider layer, same engagement workspace
conventions, same test discipline.

## The reflex layer — System One decisions (2026-10-02, skeleton live)

`agent/lib/reflex/` — judgment as classification, prose as the only
generation left. The operator's diagnosis drove the design: the
supervisor "mostly bullshitted" because a GENERATOR was asked to find
problems — RLHF helpfulness means it always has an opinion, so healthy
traces got invented problems at 100% apparent certainty and nothing
measured whether nudges helped.

Structure (concepts as packages, not files):
- `core/` — the spine: DecisionClient (transport-agnostic: a System One
  HTTP adapter + a deterministic abstain-biased local classifier so the
  pipeline is honest before laya-mlx lands; qset-versioned — a stale
  model DISABLES, never misfires), the question registry (6 questions,
  each with ABSTAIN dominant; this file is the labeling contract),
  pure feature builders, and shadow mode (the outcome-labeled corpus:
  features + classifier answer + legacy answer + did-the-next-N-turns-
  improve — the label set for training AND intervention-precision, the
  number that proves the supervisor stopped bullshitting)
- `supervisor/` — tier-0 rules (kept), tier-1 verdict classifier with
  borderline readings needing next-interval timeline confirmation,
  tier-2 GROUNDED phrasing: one LLM call that receives the confirmed
  verdict + the features that fired and must answer OFFER-shaped — a
  generator handed the decision cannot invent the problem
- `drifter/` — objective alignment as a per-turn TIMELINE (3-of-4
  sustained gate kills single-turn false positives); feeds the
  supervisor digest as _drift_score, no separate voice
- `oracle/` — ms-priced triage (anomaly CLASS gating the LLM hypothesis
  call — most outputs are none, so most calls die before they exist)
  and adjudication (confirm/deny/need_more; a high-confidence deny
  auto-distills into the cheatsheet as a dead battery — the systems
  close the loop on each other)

All behind `decision` config (default OFF = exactly yesterday). The
agent_graph seams are wired config-gated. 20 tests pin: abstain
dominance on healthy traces, qset-mismatch disable, invented-choice
rejection, timeline confirmation, grounded phrasing refusal-as-silence,
deny-distillation, and precision measurement (0.0 after a bad nudge —
honest None until outcomes close).

## laya-mlx hooked up (2026-10-02)

The System One engine is wired end-to-end and configurable from Settings:

- `decision.engine` DEFAULTS TO `laya-mlx`: an in-process Agent over the
  local checkpoint (`~/laya-mlx`; laya-mlx+mlx installed in the venv;
  the model loads once, ~0.1s, then answers in ~25-120ms). `http` exists
  for remote decision servers; `local` is the deterministic fallback.
- Questions now carry their System One phrasings IN the registry —
  instructions reference state FIELDS and criteria carry per-label
  DEFINITIONS (laya's trained format): the same file is the fine-tuning
  contract (labels = the criteria keys).
- SETTINGS ("Decisions — System One" section): System One on/off, the
  engine (the row doubles as a live model-status line — "[loaded]" /
  "no model at <path>"), the model folder, the http endpoint, and the
  on-failure policy. Flat editor keys bridge the nested `decision`
  block; pack fills sane defaults (engine=laya-mlx, ~/laya-mlx, local
  fallback) and drops the http endpoint when unused.
- Engine misses (no model, bad path, invented choice) fall to the
  deterministic classifier — never a crash, never a guess.

Base-model honesty: laya answers structured and fast, but its
discrimination on OUR questions is weak until fine-tuned (expected —
out-of-distribution). The confidence thresholds + abstain gates keep it
safe in the meantime: low-p answers simply don't act, and shadow mode
logs everything for the training corpus. Live proof: healthy trace →
p=0.31 below threshold → no intervention; model answered in 82-231ms.

## Knowledge hardening + KG live (2026-10-07)

0. **The KG is WORKSPACE-FIRST (2026-10-23, the no-hardcode ruling)**:
   the live graph is `<workspace>/intel/knowledge_graph.json` — the
   in-tree `knowledge_graph.json` is a READ-ONLY neutral seed (lab +
   example.com + target-agnostic methodology), copied to the workspace
   on first boot. The write path used to point at the package dir,
   which committed live-engagement API keys and target intel into git
   (the file was later gitignored, but history holds it — flagged to
   the operator 2026-10-23). Landscape platform-maps moved the same
   way: `_KNOWN_FAMILIES` ships EMPTY, operators carry their maps in
   config `landscape_families` (dict = per-platform, list = any
   target). `suijin/tests/ops/test_no_hardcode.py` is the standing
   tripwire (synthetic-only secrets, no machine paths, no package-tree
   writes; engagement tokens from local-only
   `agent-vault/targets.txt`). Ruling codified in AGENTS.md.
1. **Neo4j KG is the live backend** (local config `kg_backend=neo4j`):
   docker container `suijn-kg` (neo4j:5, volume `suijn-kg-data`, bolt
   127.0.0.1:7687, creds in `suijin/.env` — NEVER config.json, that file
   is committed). `get_backend` reads env → `.env` file → config and
   falls back to JSON loudly on a dead server. Migration script:
   `scripts/kg-migrate-to-neo4j.py` (idempotent). Fresh installs stay
   JSON (the zero-dependency default); ADR-002 carries the supersession
   note. Gated live tests: `test_red_knowledge_graph.py` (skip without
   :7687; the autouse fixture also invalidates the backend cache — env
   alone leaks a memoized neo4j into json tests).
2. **Enforced KG read**: every think context injects
   `knowledge_graph.summary(target)` as a `## KNOWLEDGE GRAPH` section
   (untrusted-wrapped, 1600-char cap) — the agent can no longer skip
   what the graph knows; `check_knowledge` stays for deeper queries.
3. **CONFIRMED fan-out** (`_on_confirmed` in exploit_catalog): a
   terminal-verified exploit auto-writes a `verified` KG constraint,
   broadcasts a `[system]` mesh line, and writes an engagement note —
   zero model choice.
4. **Machine comms**: `mesh.set_phase` broadcasts `node-P phase → X`
   on every transition (system attribution); the GROUPCHAT prompt
   carries the hard cadence rule (≥1 broadcast / 5 iterations).
5. **Lab/real isolation**: `attack_memory.is_lab_target`
   (loopback/*.local) gates `what_worked` class-transfer (lab catalogs
   never advise real runs) and `record_finding`'s new FLAG{} guard
   (lab markers on real targets are REJECTED as contamination).
   Cheatsheet entries carry `origin` provenance from the engagement dir.
6. Console: header strip cost carries `$` again.
7. Docs de-blued everywhere except release notes (frozen history) and
   the discontinuation ruling note in this file; README rewritten
   product-first (verified facts only, PyPI/pipx line is real).

## System One assessor + critical finder + adversarial depth tools (2026-10-12)

1. **The heuristic detector stack is REPLACED by the System One
   assessor** (`agent/lib/reflex/supervisor/assessor.py`, interval 5):
   laya evaluates a semantic evidence brief (objective, last-10
   actions, findings/footholds, untested hypotheses, pending chain,
   high-value targets, cost) and returns a verdict — `deep_dive` /
   `chain` / `hypothesis` / `drift` / `stall` / `radio_silence` — with
   reason+action+priority. Fallback ladder: laya → local LLM (semantic,
   silence-dominant) → the two loudest deterministic detectors. Silence
   is the default; every assessment/abstention shadow-logs to the
   fine-tuning corpus.
2. **The critical finder**: `reflex/app_model.py` auto-populates an
   endpoint/trust-boundary/high-value/state-machine model from browser
   network capture (`mcp_browser_net_*`); at ≥8 endpoints + ≥1 trust
   boundary, `reflex/hypotheses.py` generates 5 impact-priced attack
   hypotheses (critical = data access / priv-esc / financial). The
   HYPOTHESIS GATE (think_node) refuses completion while CRITICALs are
   untested — the queue is a gate, not a suggestion. `reflex/
   chain_composer.py`: a CONFIRMED sets `_chain_pending`; unexplored
   for 2 turns → CHAIN REQUIRED escalation + auto chain hypotheses.
3. **Adversarial depth battery** (`tools/lib/adversarial_tools.py`,
   `race_test.py` — all routed in dispatch + catalog): `auth_forge`
   (10 JWT forgery variants vs live target), `cred_matrix` (every
   credential × every object — the systematic IDOR finder), `race_test`
   (N concurrent, response divergence/status split/timing anomalies),
   `graphql_attack`, `mass_assign`, `param_pollute`, `header_enforce`,
   `origin_confuse`. Built from field-run evidence: surfaces found,
   depth never tested.
4. **Drive Stage 2/3 wiring live in think_node**: epistemic observe per
   result, reflex probes on unresolved surprise, self-model identity
   from CONFIRMEDs, bandit credit on CONFIRMED (conservative
   attribution, `drive_policy.json`), one-shot continuation veto in the
   completion gates.
5. **mcp_playwright expansion**: network capture, cookies, storage,
   request interception, tabs, console, smart wait, auth-state
   save/restore, shadow-DOM-aware queries — the browser is now a
   first-class sensor feeding the app model.
6. **Operator TUI**: borderless header strip, engagement-TUI-shaped
   command output panes (args capped, output verbatim-capped), token
   gauge breathing in demo, session-fetched token counts.
7. **bbp_report.py**: per-finding BBP submission files (CWE/impact/
   remediation maps) + system-difficulties report. **ai_env.py**:
   `state` (persistent agent variables), `tools` (categorized
   reference), `help` (runtime man page).
8. Suite at this commit: 2863 passed / 9 skipped (gate `-m "not ai and
   not slow"`, 15 deselected); `test_migration_e2e`
   can flake on port collision under the full run (passes isolated) —
   known, benign. Operator TUI round (same day): history-recall ↓ crash
   fixed (readline semantics), /command dropdown + note moved to their
   own `floats` layout region so the input box (fixed size=3) can never
   be cropped away — pinned in tests/console/test_operator_tui.py §7.
   v7.2 round: restructure RESTORED after mistaken rollback (rebuild
   notes in the vault); all 26 old-layout test pins fixed; backup
   script keeps a .prev generation now.

Restore note (2026-10-12): the working tree was rebuilt from the
GitHub root layer + a pre-wipe tarball of the package dir after a
destructive cleanup; the tarball's `suijin/suijin_agent` symlink
stale residue was removed (runtime `ensure_workspace_layout` owns
that path). Engagement data under the old workspace did not survive;
code and config did.

## 2026-11-06 — auth sessions, live cookie jar, swarm comms (worker round)

1. **`suijin auth`** (`server/tools/lib/auth_session.py`): named browser
   sessions per target. Bare invocation = THE LIVE COOKIE JAR — a
   persistent-profile browser (~/.suijin/config/auth-profile, logins
   survive runs) that snapshots every cookie jar per registrable domain
   every 4s and registers auth-looking jars as credentials LIVE
   (replace-by-service; append-dedupe served stale jars after
   re-login). `--paste` (stdin cookie string, __Secure-/__Host- prefix
   handling — Chromium rejects them otherwise), `--check` heartbeat
   (alive / EXPIRED on login-redirect or 401/403 / redirect-loop),
   `--name` second accounts, `--list`. Sessions are local-only
   (config/sessions/, gitignored by location) and shared across ALL
   engagements.
2. **Engine ladder** (`_launch_browser_context` + `_stealth_playwright`):
   real Chrome by launch-probe (channel="chrome" resolves per-OS: app
   bundle on macOS, PATH binary on Linux) → bundled Chromium → "use
   paste mode" on engine-less hosts. patchright (protocol-level stealth
   fork, drop-in, pip-only, mac+linux, shares the playwright browser
   cache) is preferred when installed — Google-class IdPs reject
   sign-ins from automation-flagged builds ("this browser or app may
   not be secure"); automation signals stripped in every branch.
   Capability checks throughout, never platform guesses.
3. **`suijin swarm say "msg"`**: publishes to every live agent's steer
   topic; agents queue the message and the worker loop injects it as an
   OPERATOR INTERJECTION user message at the next turn boundary
   (worker_loop's on_turn hook). Printing alone never reached the LLM.
4. **Worker loop is one implementation** (`server/swarm/worker_loop.py`):
   process agents and coordinator inline agents both call it. Tool
   catalog rides the system prompt; truncation-aware correctives
   (write_file-then-run for long payloads); parse failure re-prompts
   (5 strikes → blocked, NEVER silently complete). swarm_turn_cap
   config key (default 40; operator set 60); `exhausted` intents requeue
   once via the health sweep.
5. **Credential bridge fixed** (tests/tools/test_credential_bridge.py):
   http_request wiped injected credentials mid-function; TWO stores
   never talked (http_replay memory dict vs file-backed
   credential_store); persist path raised NameError swallowed as an
   error-string return. One store now; register persists; apply falls
   back to the shared store. creds_add_replace for live jars.
6. **Browser extra**: `pip install suijin[browser]` (playwright +
   patchright, both optional). Base installs stay browser-free;
   paste-mode auth and all HTTP tools work everywhere. Declared in
   pyproject [browser] extra + requirements.txt notes.
7. **Health sweep fixed** (age-vs-timestamp arithmetic — the sweep could
   never requeue anything; see vault bugs-ledger 2026-11-06). Dead-row
   cleanup keeps `swarm status` counts honest.
8. Suite at this commit: swarm 27 + mesh 9 + auth/bridge 14 green;
   console 203 green. The combined tools+console run remains
   order-dependent flaky (41 failures without the new files, 7 with —
   pre-existing, known).

## Web console (2026-11-14 night build)
- Vendored 0 frontend → `server/web/console-ui/` (Suijin-branded, engine shim
  vendored at vendor/engine). Build: `npx vite build` → served by `suijin web`
  (FastAPI, SPA catch-all, adapter included). Dev: vite :5174.
- `suijin/server/web/console_api.py` — the API adapter (40+ routes) between
  the dashboard contract and Suijin's real state (swarm.db, engagement tree,
  bus, config.json, tool registry). Sessions = swarms; "New swarm" starts a
  real hunt; composer steering = /cmd/steer; settings pills = real config keys.
- STRUCTURED MODE `suijin/server/swarm/structured.py`: M1 leader → M2
  supervisors (recon/exploitation/coder, +1 per 4 workers) → M3 workers.
  Model cascade m3→m2→m1 (config model_1/2/3; model_1 required). Watchdog
  forces supervisor reports every ~3 min. CLI: `suijin swarm hunt "<t>"
  --structured --workers N --node host|box`. Comms over the existing mesh
  tools (mesh_dm/mesh_read/mesh_broadcast).
- `suijin/server/api/server.py` — `suijin api start` (port 8741): API key on
  every route (401 without; key file ~/.suijin/config/api_key.json), read-only
  data layer.
- UI journey test: `tests/ui/ui_journey.py` (Playwright, 16 checks) +
  `tests/fixtures/demo_target.py` (local-only fixture, 127.0.0.1:8099).
- Swarm workspace root is ~/.suijin (NOT ~/.suijin/config); findings.created_at
  is SECONDS. sqlite: WAL forbidden (container-mount proven); one coordinator
  only. Details: agent-vault/bugs-ledger.md (2026-11-14 night).

- Models page + hero (2026-11-17): `/models` is the one provider surface
  (`/connections` redirects). Catalog = registry + native entries merged
  by key; logos served from `/api/console/provider-icon/{domain}` (server
  fetch + workspace cache, letter fallback); live model list from
  `/api/console/providers/{key}/models` with the .env key; effort options
  come from models.dev `reasoning_options` per model; the request JSON is
  the stored override at `~/.suijin/config/providers.json`. Hero composer
  shows runtime logo + model + effort and rotates 20 prompts per load.

- Autonomous-hunt guardrails (2026-11-17): provider registry gained a
  `numes` OpenAI-compatible spec (api.numes.ai/api/v1, NUMES_API_KEY in the
  local .env); the engine strips `reasoning_effort` for numes (its free
  models expose a reasoning toggle, not effort). Transport rails now
  enforce scope + 1 request/2s at both `http_request` and `execute_terminal`
  (the rate bucket had a dict-vs-float bug and never throttled), scanners
  and scripted network loops are refused, and browser navigation shares the
  scope rail. In-process `memory_guard` (agent/coordinator/web) plus a
  host watchdog cap RSS, dedupe one agent per engagement, and stop hunts at
  a deadline. Tests: `test_memory_guard.py`.

- Models page Diagnostics (2026-11-18): rows show icon + verified/failing/
  untested state from a per-provider ping (/api/console/providers/{key}/
  verify); settings model choices come from /api/console/available-models
  ('provider:model' values, routed cross-provider by model_routing's
  provider:model split); findings console adapter gained POST triage,
  workflow and impact-assessment routes plus GET /api/findings/export,
  backed by local finding_*.json ledger overlays (swarm.db untouched).

### 2026-11-20 — SEVERITY-AI retirement (commit 0d34465a)
Operator ruling executed: the severity calculator is fully retired — no `system_severity` stamping, no `_honesty_check()` cap, no CVSS recomputation of the severity word in `classed_title`, and `_write_finding` renders the stated severity verbatim. The AI-defined severity is the severity of record. Both legacy functions remain defined (import compat) but are dead on every path. Also this commit appended the ACTIVE ENGAGEMENT PROTOCOL to AGENTS.md (autonomous overnight hunts while operator-approved).

### 2026-11-23 — exploit catalog: one yaml, one call

`suijin/server/tools/lib/exploit_catalog.py` leads with `verify_exploit_yaml()`:
an `exploit.yaml` (name, class, target, cvss, description, commands,
Expected_result) is written with `write_file`, then `catalog_exploit {"yaml":
<path>}` runs it and answers only SUCCESS or NO. On NO the AI edits the yaml
and calls again with the same `exp_id`. The tool writes `EXP-NNN/{exploit.yaml,
finding.md, run-N.log}`, checks the marker in non-echo output, and records the
finding itself (stable id, clean title, AI-defined severity). Legacy inline
`commands=`/`expected_result=`/`entry_id=` calls still work. Operator surface:
`suijin exploit --yaml <file>` and `suijin exploit --demo` (built-in local
fixture). Mutation tools are exempt from the action cache so retries are real.

### 2026-11-23 — Memory page is a markmap reading surface

`console-ui/src/pages/memory-mindmap.tsx` (and the `~/0/packages/web` twin) now
renders the library as a nested markmap instead of a cytoscape graph:
`MEMORY → External/Internal/Unsorted → target → Info/Endpoints/Vulnerabilities
→ entry`. Targets render folded (markmap depth 1 = root, `initialExpandLevel: 3`
shows categories + targets, hides branches); a click reveals a target's three
branches, and vulnerabilities show their full rule + evidence in the map. The
tree is built deterministically from node kinds/ids (endpoint nodes are mapped
to their target through the endpoint branch edge), so the graph's noisy
cross-links can't reorder the hierarchy. Vuln count badges ride target and
Vulnerability branch nodes. The existing Back / fit / reload / target-and-entry search /
inspector / Escape / pan / zoom / dark surface are kept; the initial view is
~0.95–1.0x centered on the root (fit-all is a button, not the entry view, so
text never lands at 4px). Deps: `markmap-lib` + `markmap-view` 0.18.13 in both
web trees. Verified headless via CDP: 869 data nodes / 87 visible (root + 3
categories + 83 targets), branch expansion, 24 full vuln entries with
evidence, search → inspector, Escape close, Back to `/chats`, zoom/pan on,
zero console exceptions. Screenshots: `/tmp/memory-initial.png`,
`/tmp/memory-expanded.png`.

### 2026-11-23 — sidebar is target folders, auto-created and populated

`host-sidebar-chat-history.tsx` (both web trees) now groups every session under
its target: **My Mac → target folder → running agents + previous engagements**.
There is no separate Targets section and no manual folder creation — the fold-
ers are derived from each session's target on every render. The console adapter
(`console_api.py`) derives the target from an explicit `target` in
`engagement.json`, else the mission text, else the slug, and exposes
`targetKey`/`targetLabel` on the session. Folder headers carry a live dot while
any agent in them is working and a chat count; collapse state persists per host.
Previous engagements for a live target are merged into the same folder (bounded
per target); sessions with an explicit target persist after their agents stop,
so the five operator-launched engagements remain as folders. Metadata writing
was keyed on the engagement FILE (the reports mkdir made the old folder check
always true, so explicit targets were never stored). Verified headless: five
auto folders under My Mac, one chat each, live dots, collapse/expand, open chat,
zero console errors.

### 2026-11-23 — Stop pauses, Delete pauses then deletes, targets are programs

The composer Stop button now PAUSES the engagement instead of killing it:
`/api/console/sessions/{id}/pause` sets `paused` on the agents, the session
reports `waiting` + `paused: true`, the composer swaps the stop control for a
Resume (play) button, and sending a message also resumes. The agent holds
between turns (heartbeat preserves `paused`); `/cmd/pause` and `/cmd/resume`
are swarm-scoped so one session cannot pause the fleet. `force-stop` remains
the kill path. Delete (`DELETE /api/console/sessions/{id}`) now publishes
`/cmd/pause` then `/cmd/delete`, hard-stops anything left, removes the swarm
rows, and unlinks `engagement.json` so the deleted chat cannot resurrect as a
folder. Target folders use the explicit program label verbatim (e.g. a brand
name), not a parsed endpoint host; a program with two engagements shows one
folder with both chats.

### 2026-11-23 — Findings queue summary + cleaner rows

The Findings page now opens with a severity summary strip (Critical/High/Medium/
Low/Info counts + `Confirmed N of M`) before the toolbar. Rows show the EXP id
as a badge, the CVSS score as a colored badge (color follows the AI-defined
severity), a subtle status dot, and the engagement chip when the backend
provides one; the generic "finding" category label is gone. The API now selects
`findings.engagement` and passes it through `_finding_group`. Catalogued titles
stay clean (frontmatter stripped at read/write). Empty/old rows show just the
title, occurrences, CVSS/EXP and workflow phase. Verified headless: summary
counts render, 38/40 rows carry EXP badges, 33 carry CVSS badges, zero console
errors.


### 2026-11-24 — runtime containment and cleanup

All runtime data now lives under `~/.suijin`; the product repo carries only
code/docs/tests. Removed target-derived source trees and screenshots from the
working tree and rewrote git history to purge them. Pruned stale engagements
and duplicate artifact roots, preserved one archived engagement with its
findings/evidence, and rebuilt the engagement index. Generated data for a live
engagement resolves inside `~/.suijin/engagements/<slug>/`; the global fallback
is `~/.suijin/artifacts/` (never `config/artifacts`). Browser profile/session
jars moved to `~/.suijin/browser/`, learned intel to `~/.suijin/intel/`.
`scripts/rebuild_engagement_index.py` writes `engagements/index.json` mapping
each chat to its target, audit trail, artifacts, and finding count.

### 2026-11-25 — platform-specific submission reports

The finding detail page exports a submission report via a dropdown:
Intigriti, HackerOne, Bugcrowd, or Other (all three). Each writes one Markdown
file to `~/Desktop`; no EXP/chat/audit/Suijin ids appear in it. Reports carry
the platform selectors (CWE, VRT, asset type, CVSS version, severity method),
summary/prerequisites/root cause, numbered steps, raw requests (redacted),
impact, remediation, and evidence filenames. `finding_submission_meta` stores
the derived metadata at catalog time; the export derives it for old rows.

### 2026-11-25 — embedded LadybugDB knowledge graph

Added a LadybugDB backend: an embedded Cypher graph at
`~/.suijin/intel/knowledge_graph.ladybug`, no Docker or daemon. The graph
opens/closes per operation with a lock retry so the web server and agent
subprocesses share it safely. JSON remains the fallback and holds the
operator-owned `methodology` branch; the memory page reads whichever backend
is configured and merges methodology from the JSON file. Switched the local
config to `kg_backend=ladybug` and migrated the current targets/constraints.


### 2026-11-25 — engagement permissions/mode, inbox, explain, detailed findings

The hero now carries **Permissions** (Recon read-only / Exploitation safe-harbour /
Maximum except-DoS) and **Mode** (BBP / VDP / CTF / Pentest) plus a `+ Files`
inbox. Settings and uploaded text files are stored on `engagement.json`; the
mission tells the agent its permissions/mode and to read `inbox/`. Pentest is
gated on an uploaded text document in both the UI and the API. Findings carry
the full multi-paragraph description from the catalog/`findings_v2`, plus
Impact and Remediation sections in the detail view. The finding page has an
**Explain** popover: a copy-ready review prompt and an analysis by the main
leader model (`model_1`) that writes a Markdown report to `~/Desktop`. If the
leader returns no text, the UI surfaces the real provider error; there is no
alternate or heuristic model in that path. The catalog YAML accepts a `description: |`
block and the prompt requires 2-3 paragraphs: what/where, root cause, impact.

## Landing = 0's native new-thread screen (2026-11-27)

The console's landing is no longer hand-built. It is **0's own new-thread
screen**, carried in `server/web/console-ui/zero/` from `0sec-labs/0` @
`78d453c0` (MIT OR Apache-2.0 — licences kept beside it, `zero/README.md` is
the provenance record) and adapted for Suijin. 233 upstream files are mirrored
verbatim apart from four mechanical path rewrites (`MANIFEST.json` lists them),
and — since 2026-11-27 — four contained hunks in `provider-icon.tsx` (model
maker icons) and one type line in the vendored engine's `dist`.

**Why a second document.** 0's landing is a 233-file closure of their source,
and 65 of those files are ones this console has modified. So the landing runs
in its own document (`zero/home.html`, a second Vite entry → `/zero/home.html`),
hosted same-origin in a frame by `src/console/hero-frame.tsx`. 0's stylesheet
can never reach the console, and no Suijin logic was overwritten.

**The bridge** (`zero/entry/protocol.ts`, the only shared file): both sides
check `origin` + `source`; the console shape-checks every submission. The frame
only collects the prompt — session creation, inbox upload, navigation,
pause/resume/delete stay in the console. The console *accepts* once a session
exists and its inbox is in place (and clears the stored draft itself, since it
navigates away at once), or *rejects* and the frame keeps the text and files
and says why. A selector change is announced immediately, so a choice survives
a reload. `console-page.tsx` changed only in its landing slot: `<HeroFrame>`
replaces the inline form, and the create-at-submit branch reads the selection
from a ref.

**Mapping, the way 0 itself slots them:** a Chat | Engagement switch sits in
0's context row where their project selector is; when it is an engagement, Mode
(BBP/VDP/CTF/Pentest) sits beside it; Permissions
(Recon/Exploitation/Maximum) sits in 0's autonomy slot where `YOLO` is; 0's
prebuilt `ModelCatalog` is fed by `/api/console/available-models` and saves
through the runtime PATCH. Three Suijin suggestions replace 0's. Composer up
to 1120px wide, never below 128px tall. Palette measured from 0's own screen
(`#101010` base, `#1b1b1b` card); the backdrop is asciify's, blue-cyan (below).

**Found by running it for real, and fixed in the backend:**
- Delete left a folder skeleton per chat, and any intent filed under another
  swarm name stayed `open` — and one open intent anywhere pauses the idle
  autopilot. `session_close` now removes intents tagged with the engagement and
  prunes the folder when no finding/exploit references it (`docs/WORKSPACE.md`).
- A cold start shelled out to the `suijin` launcher on PATH, starting the
  coordinator under the system interpreter (which has no `ladybug`), and
  `swarm init <title>` filed the title into the DEFAULT swarm. All three
  starters (hunt, `swarm init`, the dashboard's Start Engine) now share
  `lifecycle.start_coordinator()`: this interpreter, logging inside the runtime
  root, ready only once the bus and schema answer. The Start Engine's old
  `python -m suijin.server.swarm.coordinator` was a no-op — the module has no
  `__main__`.

**Verified:** `tests/hero_journey.py` 42/42 (real browser, state-changing
endpoints stubbed), `tests/hero_layout.py` all measurements at five window
sizes (overlap / clipping / overflow / click-blocking / WCAG AA contrast),
`vitest` 6/6, 18 new pytest cases (incl. a router-level check that DELETE is
bound to `session_close` — a helper once took its decorator), one real cold
start 15/16 (the miss was a bad check: `ps` cannot tell a venv from the system
interpreter; the proof is the coordinator being a child of the console process).

**Since built (see the next section):** the reasoning typewriter and chat mode.
**Known:** when the free provider stalls, a session
shows `working` with nothing visible for minutes. Agent log/pid files still go
to the system temp dir (`suijin-agent-*`). 19 pytest failures predate this
work: 11 stale (CLI providers probe, verb drift `api`, a dangling import in
`suijin/main.py`, module globals, static prompt 210 tokens over budget, doctor
rows) and 8 that only fail from test-order leakage in the combined run.

## Chat mode: the landing is a chat by default (2026-11-27)

Operator rulings: *"By default it's new chat not new engagement unless specified"*,
*"make sure new chat, and new engagement exist"*, *"when shit isnt related to
engagements then its general"*, *"ensure that it typewriters reasoning."*

**A session has a kind.** `kind: "chat"` is a conversation with Suijin; an
engagement is the hunt, as before. `sessions_create` takes `kind` and a chat
ignores `spawn`. Anything not about a target — chats, questions, tool tests,
no-target runs — files under one sidebar folder, **General**; only sessions with
a real target (explicit, or a host in the title) get a target folder. The kind is
deliberately not persisted: the landing is a chat every time it opens, unless the
link says `?kind=engagement`.

**One turn** (`suijin/server/web/chat_operator.py`, `run_turn`): the leader model
(`model_1`, what the landing's picker writes) speaks the JSON decision the hunt
workers already use — `{"thought", "reply", "action": "use_tool" | "done",
"tool_name", "tool_args"}`. `reply` is the conversation; a tool step runs through
`dispatch.route_tool` (same guardrails as the hunt) and loops; prose without JSON
is a valid answer. Two operator tools exist only for a chat turn:
`start_engagement` (through the same door as the landing) and `suijin_doctor`.
The prompt's tool list is the catalog's plain "ALL AVAILABLE TOOLS" section —
never the hunt catalog whole, whose header is hunter doctrine. Everything that
touches the outside is injected, so the turn is decided by tests without a
provider. A deliberate exception to the one-loop ruling (`agent-vault/decisions.md`).

**The console around it** (`console_api.py`): records go to `events.jsonl`
through `EventLog` (new kinds `user.message`, `assistant.reasoning`) and are
ringed the moment they exist; `_chat_streamed` keeps the poller from ringing them
again. A message that arrives mid-turn is read before the next step or starts the
next turn; the running mark is handed over without a gap. Stop sets a cancel flag
and never publishes `/cmd/pause` for a chat. Two scanner fixes came out of this:
chats were never listed, and no-target runs vanished once their agents stopped.

**The typewriter** (`src/console/typewriter.ts` + `use-typewriter.ts`): pure,
tested arithmetic. Reasoning 320 chars/s, replies 700. Text types only when its
turn is fresh — working now or seen working in the last 1.5 s (a final reply
lands in the same poll as "done") — and never under reduced motion, never on a
reload, and never when it appears together with the transcript (opening a chat
mid-turn must not retype it). A finished thought is a `{ type: "reasoning" }`
content block, so it survives a reload.

**The landing's look**, from the operator's screenshots: asciify-engine's studio
background (their template vendored verbatim, MIT; ink `#22d3ee`, opacity 0.35,
hover-follow off — "kinda hard to read"), the Sui mark beside the wordmark above
the composer (`zero/entry/sui-mark.png`: the source `sui.png` is a smoothed export;
its true 20 × 18 grid was recovered, 743 bytes, crisp at 2 px a block), no glow.

**Model maker icons.** 0's table knows 14 brands; Suijin lists 101 makers and 159
of 626 models fell to one generic chip. `zero/entry/brand-art.ts` adds 27 makers
with vector artwork generated by `scripts/fetch_brand_art.py` from the libraries 0
itself uses (Lobe Icons MIT, Simple Icons CC0); the backend sends cached site
icons and GitHub / Hugging Face avatars for the rest; an initial is the last
resort. An incomplete model list is no longer cached for five minutes.

**Sidebar collapse.** The button flipped a flag but the rail's hover-preview kept
it drawn open while the pointer was on it. Measured 48/256 before, 48/48 after.

**Verified:** pytest 59 in `suijin/tests/server` (chat turn 25, console wiring 19,
models list 1, delete 9, coordinator 9 — minus overlap), `vitest` 19, `tsc` clean
for the landing entry and exactly the 12 pre-existing errors elsewhere,
`tests/hero_journey.py` + `tests/hero_layout.py` (both kinds, five window sizes),
one REAL conversation in a real browser: created as a chat, ran `suijin_doctor`,
replied, typed out, spawned no agent, filed under General, survived a restart.

**Known:** the free model is slow — that two-step turn took 150 s end to end.

## Everything into the UI: swarm, engagements, data, JSON (2026-11-27 night)

Operator: *"find swarm logic, engagement logic, and all suijin logic, everything, make it
into a UI … clickable, usable"*; *"all suijin data needs to be available locally through
organized buses and data flows"*; *"all json needs to be prettified colored"*.

**Four reads came first** — the swarm runtime, the console's API and UI surface, every
local data store, the box hypervisor and all other subsystems — so the UI was built on what
the code does, not on what it was assumed to do.

**The data hub** (`suijin/server/web/data_hub.py`, `data_stores.py`, `data_api.py`). One
registry of every place Suijin keeps data — 41 stores over config, the swarm database,
engagements, the knowledge graph, tools, the mesh, system and logs — reachable the same
way from Python (`data_hub.catalog() / read() / kinds() / watch()`), from HTTP
(`/api/data/catalog`, `/api/data/stores/{id}`, `WS /api/data/stream`) and from the mesh bus
(`/data/query`, `/data/change`). It is read-only, never fatal, names no machine path, and
keeps secrets in the process: a key is masked when its whole name, or its last `_`/`-` part,
is a secret word (`password`, `neo4j_password`, `api_key`, `OPENAI_API_KEY`), while a bare
lowercase `key` is a name and `max_tokens`, `token_count`, `keyEnv` are settings; strings are
masked by pattern too. Engagement folders are registered as they appear and forgotten as they
go. The catalog is polled, so nothing slow runs on a request path: the tool list uses
`list_route_tools()` (never `get_tool_catalog()`, 14 s), slow sources carry a TTL cache,
system stats come from a background sampler (`collect()` blocks about two seconds on macOS).
Catalog 1.3 s cold and 15 ms warm.

**The operations API** (`suijin/server/web/ops_api.py`, `box_status.py`). `GET
/api/ops/overview` is the whole board — coordinator, agents, intents, fleet, findings, mesh,
jobs, daemons, browser, system, sessions, boxes — gathered section by section so a failing
source is named in `errors` and the rest still answers (10 ms). Engagements: every folder
with its real contents (journal by kind, files, exploits, findings); the agent's userland
(`home/`, `tmp/`) is not browsable. Control publishes on the bus topics the terminal uses:
queue work and take it back, pause/resume/stop/steer one agent, one swarm or the fleet,
start the coordinator, `swarm stop` ("Stop everything", typed confirmation), summon and
drain the warm pile, stop a sandbox, build the template image. Box state comes from a
background cache and the engine is probed only when its socket already exists — asking for
state can never start OrbStack.

**The pages.** `/swarm`: six headline stats and one panel per subsystem — the roster (lane,
state, heartbeat, uptime; pause, resume, steer, stop, log), the queue, boxes and the warm
pile, the coordinator, fleet settings, the mesh, this machine, background jobs. `/engagements`
and `/engagements/:id`: every hunt, chat and legacy run, with Overview / Journal / Files /
Exploits; a 4,000-record journal pages, filters by kind and searches, and any record opens as
JSON. `/data`: the registry browsable, with the Python door printed beside it. The old
`/scope` leads to engagements.

**JSON everywhere** (`src/console/json-pretty.ts`, `json-view.tsx`). Tool output was cut to
2,000 characters on the server, so no JSON could parse; the cap is 16,000 and the page
renders a collapsible, coloured tree (fold-all, wrap, copy, secrets masked, at most 200
children drawn). It is wired into the transcript's tool output, approvals, the tool catalog,
the inspector, the event timeline, finding evidence, the engagements journal and files, and
every row of the data browser.

**Twenty defects fixed on the way, each with a test** (`test_ops_api.py`,
`test_subsystem_truth.py`): a literal route shadowed by `/swarm/{action}` so "Stop
everything" did nothing; the dead `/api/state` routes; a boot orphan sweep that never ran
(now only a re-parented pid with no coordinator); a redaction rule that masked four setting
names and missed `neo4j_password`; `summon N` ignoring N; the doctor's hardcoded facts; the
engagement index skipping folders without metadata; evidence written one level too deep; a
hook-order crash on leaving the memory map; and the last twelve TypeScript errors.

**Verified:** `pytest suijin/tests/server` 101, `vitest` 37, `tsc` zero errors, and a browser
journey that uses the real controls — queues work and takes it back, pauses and resumes the
fleet, starts the coordinator and stops everything — then leaves no coordinator, no agent, no
intent and no pause flag behind.

**Known:** the stub routes and the frontend calls to missing endpoints that predate this
work; the repo-wide lint and format baseline. See the next section for the real test baseline.

## Continuation (2026-11-28): no engine wakes, the rest of the swarm doors, JSON in answers

**Suijin never starts a container engine.** After finding OrbStack running ("turn off the
fucking orbstack too"): on macOS the docker CLI starts OrbStack whenever it runs, even for a
read-only probe. The first guard asked only whether a socket file existed, and this machine keeps
Docker Desktop's socket file while the docker context points at OrbStack — so it let docker
through and woke OrbStack a second time. Now `hypervisor.engine_socket()` answers only when
something is **listening on the endpoint docker would actually use** (a connect, not a stat:
`docker_endpoint()` + `_live_unix_socket()`), and every background path asks it first: the web
console's box status, the data layer's boxes query, and `swarm stop` (`_docker(wake=False)`,
`engine_available(wake=False)`). Proven with OrbStack stopped: every route answered and it stayed
Stopped. An engine is started only by a person, on purpose. Rule and tests:
`.opencode/wiki/concepts/no-engine-wakes.md`, `test_subsystem_truth.py`.

**Every feature of the terminal control hub is reachable from the Swarm page.** Added: *Start
agents* (`POST /api/ops/swarm/spawn` — host agents as real processes the way hunts spawn them,
under the memory guard; boxed agents in sandboxes only when an engine is already listening), *Set
a mission* for a swarm or one agent (`/api/ops/swarm/mission`, `/api/ops/agents/{id}/mission`),
*Say on the mesh* (`POST /api/say`), per-swarm pause / resume / stop, *Queue anyway* when the
prior-work gate refuses (it states its reason), and *Structured teams* (the leader / supervisor /
worker hierarchy of each engagement, each member marked by whether it still has a heartbeat).
Literal routes are declared before the parameterised `/swarm/{action}` and `/agents/{id}/{action}`
— a path parameter declared first swallows every literal under it.

**JSON in answers and thinking.** A paragraph in an assistant message or in the reasoning that is
nothing but a JSON object or array renders as the same collapsible, coloured tree a tool's output
gets; prose stays prose; while the text is still streaming it stays text (no flicker on a
half-arrived object). Reasoning now renders through the shared Markdown renderer. Pinned by
`server/web/console-ui/test/markdown-json.test.ts` (8 tests).

**Small truths.** A folder counts as an engagement only with metadata or a journal (an empty
leftover `_default` was being listed). The boxes panel says "not running" when no engine is up and
"not checked" for an OrbStack machine rather than a confusing "not a container". A chat's overview
no longer claims agents or hunt settings it does not have.

**The real test baseline.** A bounded run of everything except `suijin/tests/redteam` (239 s,
nothing left running): **2,984 passed, 128 failed, 37 errors** — 165 failing tests in 44 files,
none naming code changed in these rounds (stale imports, the prompt budget, a dangling import in
`suijin/main.py`, environment-dependent doctor checks, order-dependent state). The list is kept at
`~/.suijin/tmp/gates-baseline/pytest-failed-2026-11-28.txt`.

**How long test runs are made now.** Never detached, never unbounded: a detached full-suite run
hung in the fuzz-loop tests and leaked to a 43 GB footprint for 11 h 45 min overnight. Each run
is one directory under a hard wall-clock limit that kills the process tree, the PID is recorded,
and after any run that is killed, its children are looked for and stopped.

**The namespace unifier leaked for subpackages (fixed).** `suijin.modules.*` and `suijin.server.*`
are meant to be one module object per file. An aliased parent's search path is the *server* tree,
so `import suijin.modules.tools.lib` found the server's own `__init__.py`, was taken for "a real
file, not a shim", and Python built a second object and executed the server package again under
the modules name — the root of the order-dependent `cannot import name X` failures. The finder now
aliases everything that is not a real file of the modules tree (packages, namespace packages, and
the case where nothing exists at the modules path), resolving the twin with
`importlib.util.find_spec` because an editable install and a zipapp resolve the package through
another finder. The alias loader also puts the server's own `__spec__` / `__name__` / `__loader__`
back on the object — the import machinery stamps the alias spec on it, which silently turned
`importlib.reload()` into a no-op. **Measured (bounded run of everything except
`suijin/tests/redteam`): 3,077 passed, 83 failed, and not one failure outside the old baseline —
82 tests that failed before now pass.** A rewrite of the finder that let a missing parent raise
turned about 160 passing tests red in one run; the bounded whole-suite measurement caught it
because the proof was repeated after the code changed. After any change to import machinery,
re-run the whole bounded suite against the baseline list. Pinned by `suijin/tests/platform/test_namespace_unifier.py`
(fresh interpreter per check, both import orders). Wiki: `.opencode/wiki/concepts/namespace-unifier.md`.

**Diagnostics tells the truth about providers.** `providers()` is built on the provider catalog:
the active provider first, every provider that has a key, and the last real verify result. The
page shows three tiers — verified (green), key saved but untested (amber), failing (red) — and
"All good" only when every configured provider answered a real call. Before, it listed a
hardcoded Z.ai plus four "unconfigured" names and never showed the provider actually running.

**Read paths no longer write, and nothing is invented.** A session's roster (a GET) used to delete
the swarm's agent rows with a heartbeat older than ten minutes — the record of stopped agents; it
deletes nothing now and shows the most recent forty stopped agents (the coordinator's own sweep
prunes dead rows; the durable history is the engagement's journal). The scan summary's
`totalAttacks` was findings × 3; it is the number of exploits actually built and run. An unknown
session action answered 200 with the unchanged session; it is now a 400 that names the action.

## Continuation (2026-11-28, afternoon): an agent is visible, and called on the right provider

**Field report.** The operator asked the chat to start an engagement. It started one; the
engagement's agent then failed every model call for nine minutes while the page said "Thinking…".

**Why the calls failed — the route.** `model_routing.apply_routing` sent a **bare** model id to
the active provider. The worker route held one connection's model id and the active provider was
another: every agent call answered `400 Unknown Model`, while the leader, on the same provider,
worked. A bare id now goes to the provider that owns it — a `<provider>_model` setting that names
it (no lookup), else the record of which configured provider lists which model
(`caches_dir()/model-owners.json`, written when the console builds the model picker).
`routing_doubt` says, before a call, when a route cannot be vouched for; a failure note names
`provider:model`. Verified with the real providers: both routes stream reasoning.

**Why nothing showed — the journal.** An agent wrote to its engagement's journal only when a
tool call COMPLETED. Now `execute_intent(on_reasoning, on_decision, on_event)`: the model's
reasoning as it streams (`assistant.reasoning`), the thought and the call BEFORE the tool runs
(`assistant.message` with `phase: "thought"`, `tool.call`), the result after, and
`phase.transition` for every state (working · pausing · paused · retrying · blocked · waiting ·
idle · stopped). A failure the operator must fix (`is_permanent_failure`) is an error in the
transcript, said once, not retried until they speak. The console builds each agent's state,
error, model and transcript from those records (`_agent_views`): a heartbeat is no longer "working".

**Messages.** Every path drains the bus through one handler (`_apply_control`): a message that
arrived mid-turn used to be dropped. A message revives a paused, blocked or spent agent.

**Delete means delete.** An agent never re-creates a missing engagement folder, and Delete prunes
again after 20 s and 90 s (never a live engagement). A chat deleted while answering winds down in
silence — Stop stays graceful; the two are told apart by the cancel flag's identity — and a chat
made again under that name starts clean.

**Titles, folders, marks, reasoning.** A session's title is its first sentence or clause (≤64
characters); the whole text stays the mission (it was cut at 200 by the chat's `start_engagement`).
The chat that starts an engagement files in the engagement's folder. The hero shows Sui again
(`console-ui/src/assets/sui-mark.png`); the sidebar keeps the newer mark. Reasoning is light grey
(`color-mix(foreground 70%, background)`) in a box that follows its own stream and yields when the
reader scrolls back (`ReasoningStream`).

**Tests.** New: `suijin/tests/tools/test_model_routing.py` (10),
`suijin/tests/swarm/test_agent_visibility.py` (15),
`suijin/tests/server/test_console_engagement_visibility.py` (19), three in
`test_console_chat_mode.py`; frontend 58. Proven live through the real doors:
`tests/console_engagement_visibility.py` (28 checks: the real Pause button, a typed message that
resumes the agent, the folder) and `tests/console_reasoning_follow.py` (the scroll, sampled every
150 ms). Both spend real model calls and are never collected by pytest.
