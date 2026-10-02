# Suijin — Project Context

> The architecture grip. Read top to bottom before touching anything.
> Last updated 2026-09-25 (hermetic test suite + no-gate boot + log-dir fix).

## Repo facts

- **Agent memory vault: `agent-vault/` (gitignored) — read it FIRST on a fresh session.** Decisions, session logs, engagement memory, roadmap state. Pointers from `AGENTS.md`.
- Repo: `/Users/williamjiang/suijin`, GitHub: `https://github.com/0xwi11iam/Suijin`
- venv: `.venv/bin/python` (Python 3.14); CI matrix py3.10/3.11/3.12
- Gates: `.venv/bin/python -m pytest suijin/tests -q -m "not ai and not slow"` (~2870 passed) + `-m slow` + `ruff check` + `ruff format`. **The suite is HERMETIC**: `tests/conftest.py` redirects `HOME`, `SUIJIN_WORKSPACE`, `SUIJIN_CONFIG` and `SUIJIN_ENV` into a session sandbox AT IMPORT TIME (before any `suijin` import — `Path.home()` is baked into `PACK_ROOTS`/`WORKSPACE_DIR`/`CONFIG_PATH` on first import, so redirecting later does nothing). A test that needs the real home must opt in explicitly.
- Version 5.7.0 published (PyPI/GHCR/Release). `plan.md` (GITIGNORED) = roadmap.
- Operator runs via `~/.local/bin/suijin` → `~/.suijin/venv/bin/python` + `~/.suijin/repo` (symlink to this tree). Their config: `suijin/config.json` IN THE TREE (provider: zai / glm-5.3, coding endpoint). `~/.local/bin/suijind` is the detached-daemon launcher.
- System-Python stale processes squat lab ports (5906/5910/5911) — check `lsof -nP -i :PORT`.
- Working-tree strays (operator's): deleted assets, 7 lab whitespace reformats, 6 trivial style diffs — do NOT commit without asking.
- `gh` CLI not authed; use `git credential fill`.

## THE INVARIANTS — breaking any of these broke the field

### Config & enforcement
1. **Config precedence**: `suijin/config.json` file value WINS over every code default (`load_config` setdefaults only; Pydantic `model_dump()` fills holes). Changing a model default does NOTHING if the operator's file pins the key — check their file.
2. **Cost enforcement lives in THREE places**: `governor.py budget_guard` (`max_cost_usd`, absent→$25 hard stop), `supervisor.py` L327+ (`cost_budget_usd`/`cost_hard_cap_usd` steering), `supervisor.py` L216 (`cost_alert_usd` flag). **0 = DISABLED at all three (via `> 0` guards — `cost >= 0` is always true, the trap)**. Operator's file now: all 0, max_iterations 100000.
3. `max_iterations` is ADVISORY (shown in prompt); the real stop is `recursion_limit` in redteamer's langgraph_config (100000, ~2 recursions/iteration) + `completion_reason`.

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
29. One workspace `~/.suijin/workspace` (override: `SUIJIN_WORKSPACE` env), resolved ONCE at import; per-engagement state under `engagements/<slug>/` (NOT `outputs/engagements`); `outputs/` holds cross-run artifacts (logs, audit_trails, reports). `artifact_dir()` validates names. Config path overridable via `SUIJIN_CONFIG`, secrets via `SUIJIN_ENV` (both read at import).
29a. **Run logs go through `workspace.logs_dir()` = `outputs/logs/`** — the same path the panels advertise. Three handlers used to write to `<workspace>/logs/`, where the operator would never look (and a nested `logs/logs/` accumulated). Use `logs_dir()`, never `WORKSPACE_DIR / "logs"`.
29b. **A crash is still an engagement**: the exception path saves a `.sje` through `CRASH_SAVER` (once-flagged, never raises) and the panel prints the resume line. The loop-breaker/`error:*` endings save via the normal end path and return to the menu.
30. Audit/diag/journal/events NEVER break the run (never raise; args digest-only; re-entrancy bounded).
31. Tool calls are data: `call_tool` returns `"Error: …"` strings, every call audited, tools namespaced.
32. Resume precedence: **engagement STATE rides the .sje; operator SETTINGS ride live config.json (current wins)** — the deepseek-402 incident.

### Blue (`modules/blueteam/lib/blue/` — still genuinely in modules)
33. `feed.ui` present ⇒ NOTHING prints from feed/blueteamer (foreign prints tear the strip). `_say` is headless-only.
34. Proxy hook order is load-bearing: log → enforce → tarpit → forward, each failure-isolated.
35. BF0: a pattern-confirmed attack NEVER exits `_execute_ai_decision` without at least a fallback tarpit; AI-down/AI-off/AI-disagrees all defend; detected ≠ blocked (honest counters).
36. BlueCommandBox handlers never raise; box `/block <ip>` (enforcement) ≠ pause `/block` (toggle). Live only on terminal/StringIO (CI).
37. Watchers `check()` stays pure; enforcement only in `apply_fast_path`.

## Architecture in one paragraph

`~/.local/bin/suijin` → `modules/console/lib/cli.py` (argparse, ~56 verbs) → no-argv → `suijin/main.py` mode selector, which opens **straight into the menu with no keypress gate** (the old "Press Enter to continue" was pure friction; banner + missing-tools notice are information, not a screen to dismiss) → Red Team → objective → `run_red_team_async` (redteamer.py), which is the FULL in-process Rich TUI: builds `SuijinAgentGraph(generate_fn=_generate_with_stream, route_tool_fn, max_iterations, run_config)` with nodes initialize→think→execute_tool→generate_response (NO supervisor node; supervision inline in `_think` wrapper: governor, circuit breaker, supervisor/oracle/drift cadences). Drives via queue-bridge astream; think reads `live_guidance.md` as last user message; tools go through `route_tool` 3-arg (repeat-guard, healing, mode gates); execute auto-backgrounds >10s into `job_registry` (daemon threads, drain once); fireteam subagents (max 5, semaphore 3, on_delta=False) drain at think start. Termination: completion_reason vocabulary {Objective-complete free text, budget_exhausted, llm_error, provider_out_of_credits, provider_failure, parse_failure, node_crash, error:*, None-on-resume} → classifier → banner. Providers: `generate()` string-returning dispatch (zai streaming SSE, coding/paas endpoints), `generate_with_failover` chain, `USAGE` tracking with priced flag. Workspace `~/.suijin/workspace` holds everything; .sje bundles = engagement state + (stale) config snapshot, resume merges current config over it.

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
- Self-service invariants (928c0bc): adjust_config allowlist ONLY (posture/temp/tokens/provider/fallback/models) — cost caps, stealth, safety modes, proxy are structurally operator-only; set_live_config stores the engagement dict BY REFERENCE (both-seam mutation: live dict + config.json disk); provider auto-chain (llm_client._auto_chain) = first key-bearing cloud + ollama, bounded to 2; write_tool targets ~/.suijin/modules/<name>/ packs (the ONLY path the loader picks up — never the vendored tree); missing-binary tool errors append install_hint. Latent-bug fixes: adversary_profile now reaches TUI runs, supervisor_interval is config-driven.
- 2026-09-25 hardening round: **the suite is now hermetic** (conftest redirects HOME/workspace/config/env at import; previously it loaded the operator's real packs, config and .env — the cause of the long-lived order-dependent `test_console_ui` flake). `SUIJIN_CONFIG`/`SUIJIN_ENV` override the settings/secrets paths (resolved at import, like WORKSPACE_DIR). `load_env()` had three field bugs, all fixed: a `.env` line starting with `=` raised `OSError(EINVAL)` and **crashed boot**; `export FOO=bar` created a variable literally named `export FOO` so the provider never saw the key; quotes were not stripped, so a real key became `'key'` and surfaced as an unexplained 401. **`.env` deliberately WINS over an inherited shell value** (opposite of python-dotenv) — it is the authoritative key store, so editing a key and restarting must not silently keep the old one. Four tests stopped touching the machine: no more `kill -9` on everything holding a hardcoded port, no `pkill -f vulnerable_app`, no pack written into the live `~/.suijin/modules`, no migration run against a path inside the repo. The bare `suijin` startup keypress gate is gone (menu opens immediately). Crash logs now land in `outputs/logs/` (`workspace.logs_dir()`), the directory the panels already advertised.

## Immediate next steps

1. Operator: re-run the pending engagement (config now: zai/glm-5.3, infinite, no caps). DeepSeek top-up optional (fallback only).
2. B1 (source audit — `modules/treeaudit/main.py` orphaned start), BF4+ waves per plan.md.
3. Before ANY commit: full gates (`pytest -m "not ai and not slow"` ~2895, ruff check, ruff format) + never commit the operator strays.
4. **Engagement confidentiality is a PERMANENT operator ruling — see AGENTS.md.** Never name a target, program, vendor, or tested host in any committed artifact (commit messages, comments, test payloads, docs). Refer to engagements generically; tests use example.com. One commit (f547d32) predates this rule and still names a target — scrub on the next commit that touches it, history rewrite only on explicit request.

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

