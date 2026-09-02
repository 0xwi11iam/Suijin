# Suijin — Project Context

> The architecture grip. Read top to bottom before touching anything.
> Last updated 2026-08-30 after the deep-read session.

## Repo facts

- Repo: `/Users/williamjiang/suijin`, GitHub: `https://github.com/0xwi11iam/Suijin`
- venv: `.venv/bin/python` (Python 3.14); CI matrix py3.10/3.11/3.12
- Gates: `.venv/bin/python -m pytest suijin/tests -q -m "not ai and not slow"` (~1898 passed) + `-m slow` + `ruff check` + `ruff format`
- Version 5.7.0 published (PyPI/GHCR/Release). `plan.md` (GITIGNORED) = roadmap.
- Operator runs via `~/.local/bin/suijin` → `~/.suijin/venv/bin/python` + `~/.suijin/repo` (symlink to this tree). Their config: `suijin/config.json` IN THE TREE (provider: zai / glm-5.3, coding endpoint).
- System-Python stale processes squat lab ports (5906/5910/5911) — check `lsof -nP -i :PORT`.
- Working-tree strays (operator's): deleted assets, 7 lab whitespace reformats, 6 trivial style diffs — do NOT commit without asking.
- `gh` CLI not authed; use `git credential fill`.

## THE INVARIANTS — breaking any of these broke the field

### Config & enforcement
1. **Config precedence**: `suijin/config.json` file value WINS over every code default (`load_config` setdefaults only; Pydantic `model_dump()` fills holes). Changing a model default does NOTHING if the operator's file pins the key — check their file.
2. **Cost enforcement lives in THREE places**: `governor.py budget_guard` (`max_cost_usd`, absent→$25 hard stop), `supervisor.py` L327+ (`cost_budget_usd`/`cost_hard_cap_usd` steering), `supervisor.py` L216 (`cost_alert_usd` flag). **0 = DISABLED at all three (via `> 0` guards — `cost >= 0` is always true, the trap)**. Operator's file now: all 0, max_iterations 100000.
3. `max_iterations` is ADVISORY (shown in prompt); the real stop is `recursion_limit` in redteamer's langgraph_config (100000, ~2 recursions/iteration) + `completion_reason`.

### Provider layer (`modules/providers/lib/__init__.py`)
4. `generate()` returns STRINGS, never raises. `"Error:"` prefix = the failover protocol (`generate_with_failover` falls through only on it).
5. `on_delta(kind, piece)` callback errors stay swallowed; `_stream_chat` returns `(status, content, reasoning, usage, body)`, status 0 = transport death → ONE `_post_chat` fallback.
6. zai: 403 = endpoint mismatch, NO retry; 402 = plan quota/credits, explain the ~5h reset. 401/402/403 all emit `_diag_llm_done`.
7. `llm_client.generate_async`: 180s hard timeout → error string. Silent (ONE Live region rule — the strip).

### Agent graph (`modules/agent/lib/`)
8. **Guidance = file-based** (`live_guidance.md` in engagement_dir), consumed once per think turn, delivered as the LAST USER MESSAGE. Two writers (input box, pause console), one file. Never `update_state` guidance.
9. **Tools dispatch via 3-arg `route_tool(name, args, config)`** everywhere; blue swaps the whole function (`route_blue_tool`), never the signature.
10. **Router keys on `_current_step.tool_name` truthiness** — bookkeeping actions (complete/ask_user/switch_skill/transition_phase) MUST clear `_current_step` or the last tool re-executes. `deploy_subagent` uses `""`.
11. `completion_reason` truthy = the ONLY normal graph exit. `.sje` resume must scrub it to None.
12. Circuit breaker: 3 consecutive provider/parse failures → forced end. Redteamer grants ONE full restart for provider_failure/llm_error only (NOT provider_out_of_credits).
13. Budget guard runs BEFORE every LM call in `_think` wrapper. messages/execution_trace hard cap 25 in `_merge_state`; execute_tool's trace return REPLACES think's opened step (iteration-keyed).
14. **Blue mode swaps THREE things together**: prompt builder (think `_blue_mode`), tool router (`route_blue_tool`), subagent tool reference (module-attr detection). A blue router with a red tool list = the BF2 bug.
15. failure-prefix sets: dispatch `_FAILURE_PREFIXES` == execute_tool_node's success check — keep synchronized.

### Red TUI (`modules/redteam/lib/red/`)
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
29. One workspace `~/.suijin/workspace`, resolved ONCE at import; artifacts under `outputs/`; per-engagement state under `outputs/engagements/<slug>` — archived on end. `artifact_dir()` validates names.
30. Audit/diag/journal/events NEVER break the run (never raise; args digest-only; re-entrancy bounded).
31. Tool calls are data: `call_tool` returns `"Error: …"` strings, every call audited, tools namespaced.
32. Resume precedence: **engagement STATE rides the .sje; operator SETTINGS ride live config.json (current wins)** — the deepseek-402 incident.

### Blue (`modules/blueteam/lib/blue/`)
33. `feed.ui` present ⇒ NOTHING prints from feed/blueteamer (foreign prints tear the strip). `_say` is headless-only.
34. Proxy hook order is load-bearing: log → enforce → tarpit → forward, each failure-isolated.
35. BF0: a pattern-confirmed attack NEVER exits `_execute_ai_decision` without at least a fallback tarpit; AI-down/AI-off/AI-disagrees all defend; detected ≠ blocked (honest counters).
36. BlueCommandBox handlers never raise; box `/block <ip>` (enforcement) ≠ pause `/block` (toggle). Live only on terminal/StringIO (CI).
37. Watchers `check()` stays pure; enforcement only in `apply_fast_path`.

## Architecture in one paragraph

`~/.local/bin/suijin` → `modules/console/lib/cli.py` (argparse, ~50 verbs) → no-argv → `suijin/main.py` mode selector (DIRECT imports, not hooks) → redteam `run_red_team_async` (redteamer.py): builds `SuijinAgentGraph(generate_fn=_generate_with_stream, route_tool_fn, max_iterations, run_config)` with nodes initialize→think→execute_tool→generate_response (NO supervisor node; supervision inline in `_think` wrapper: governor, circuit breaker, supervisor/oracle/drift cadences). Drives via queue-bridge astream; think reads `live_guidance.md` as last user message; tools go through `route_tool` 3-arg (repeat-guard, healing, mode gates); execute auto-backgrounds >10s into `job_registry` (daemon threads, drain once); fireteam subagents (max 5, semaphore 3, on_delta=False) drain at think start. Termination: completion_reason vocabulary {Objective-complete free text, budget_exhausted, llm_error, provider_out_of_credits, provider_failure, parse_failure, node_crash, error:*, None-on-resume} → classifier → banner. Providers: `generate()` string-returning dispatch (zai streaming SSE, coding/paas endpoints), `generate_with_failover` chain, `USAGE` tracking with priced flag. Workspace `~/.suijin/workspace` holds everything under outputs/; .sje bundles = engagement state + (stale) config snapshot, resume merges current config over it.

## Session history (what's done)

- v5.6.0→v5.7.0 published; BF4-BF7 (cases/dossiers, retention/hunt, metrics, learning), deathmatch scenarios, catalog_exploit w/ severity, .sje bundles, PTY rig, diag logging, provider streaming, guidance-as-last-message, workspace restructure — all committed.
- 2026-08-30 session: infinite iterations (BOTH model default 100000 AND operator file 100000), cost enforcement 0=disabled at all three sites (governor `or`-swallow fixed, supervisor `>0` guards), cursor-left input box, crash visibility (reader re-raise + red panel + press-Enter + ui.stop-first), 402 instant-kill + banner, resume config merge (current wins), provider-chain print at engagement start, `len(trace)` NameError fix (was killing all saves on early crashes), test_ai_calls marked ai+slow, pause-console/termination input() safety verified (reader stopped first).
- Fleet session (Aug 31): provider registry (24 providers — 13 cloud table rows, openrouter aggregator, 5 local keyless, custom:<name> LAN boxes via config custom_providers); _compat_call = the ONE generic OpenAI-compatible engine; M-token display (_fmt_tok 2.40M); bypass_403 battery tool (~24 variants through http_request pacing); code_harness dev loop (sandbox per attempt, python auto-pip triage, VERDICT PASS = finding evidence). Commits: 1efdeb4, d66087f.
- Registry invariants: adding a provider = a ProviderSpec row, never code; local specs are never priced (governor can't stop on free); cloud rows activate on key presence in .env; partial-config provider rescue (generate + generate_async merge real config under provider-less dicts — the deepseek-402 root cause).
- Weaponizer invariants (cc6ec66): escalation is PROPOSE-ONLY (CONFIRMED finding → ready deploy_subagent task in context, model decides); foothold predicate (uid=/creds/shell-class CONFIRMED) FORCES exploitation→post_exploit with doctrine swap; what_worked = the only positive-memory reader (CONFIRMED by target + class transfer); memory keyed by host[:port] via target_key(), never objective prose; payload_mutate is analysis-only; bench failures → learnings.md → GYM NOTES in think context.
- Self-service invariants (928c0bc): adjust_config allowlist ONLY (posture/temp/tokens/provider/fallback/models) — cost caps, stealth, safety modes, proxy are structurally operator-only; set_live_config stores the engagement dict BY REFERENCE (both-seam mutation: live dict + config.json disk); provider auto-chain (llm_client._auto_chain) = first key-bearing cloud + ollama, bounded to 2; write_tool targets ~/.suijin/modules/<name>/ packs (the ONLY path the loader picks up — never the vendored tree); missing-binary tool errors append install_hint. Latent-bug fixes: adversary_profile now reaches TUI runs, supervisor_interval is config-driven.

## Immediate next steps

1. Operator: re-run the arbonia engagement (config now: zai/glm-5.3, infinite, no caps). DeepSeek top-up optional (fallback only).
2. B1 (source audit — `modules/treeaudit/main.py` orphaned start), BF4+ waves per plan.md.
3. Before ANY commit: full gates (`pytest -m "not ai and not slow"` ~1898, ruff check, ruff format) + never commit the operator strays.
