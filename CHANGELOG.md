# Changelog

All notable changes to Suijin.

> **Note**: this project was renamed **Medusa -> Suijin** at v2.12.0.
> Entries below were written under the Medusa name at the time; command and
> path examples have been updated to the new names.

## v6.6.0 — The Web Evidence Engine

Every testing claim has a load-bearing tool underneath it. The agent
stops improvising web tests through prose and starts testing with
structured data, mechanical breadth, and evidence gates.

### http_replay — payloads travel as DATA
- 15 mutation ops (add-query enables HPP, body-set-field dot-paths,
  set-method/target/path-param), 12 composable codecs (incl. **tab**:
  url-encode with %09 spaces — slips separator-class WAFs)
- **compare mode**: baseline + exploit + structured DIFF in one call —
  the 3-gate protocol made mechanically cheap ("no measurable
  difference = NOT a finding" is returned, not remembered)
- **credential swap**: strips the 7 common auth headers, injects a
  named set — the IDOR/vertical-authz primitive
- sweep (≤50 paced values), raw TCP/TLS verbatim bytes
  (smuggling/desync), module-level 5000-request budget + AIMD limiter,
  scope guard, curl-equivalents + DBMS error signatures everywhere

### inject_probe — the battery+facts engine (never an oracle)
No 'vulnerable' field exists by design. xss: 11-tag survival battery +
20 weaponized payloads with sink-CONTEXT classification. ssti:
`7919*6841` in 9 syntaxes — product-present + literal-absent = evaluated.
sqli: DBMS fingerprints + boolean pairs against a MEASURED noise floor.
lfi: file content-signatures × 11 traversal shapes verbatim; a read
must make the signature APPEAR. WAF-blocked returns "NOT evidence of
safety — escalate."

### web_session — the cross-credential session model
Every governed send is auto-attributed and recorded. summary = the
access-control worklist: endpoint SHAPES reached by 2+ credentials, ID
fields DIFFERING per credential (the IDOR substrate with the exact
replay to fire) + hidden params (request fields the UI never exposed —
mass-assignment targets). Browser snapshots capture the UI form-field
truth that powers the correlation.

### The completion gate + coverage ledger
- Model-initiated `complete` is REFUSED while ≥2 untried surfaces or
  priority coverage cells remain — "stopped with 7 untried surfaces" is
  structurally impossible now
- coverage_check: (asset × vuln-class) ledger; tested_not_vulnerable
  REQUIRES evidence (a note without a sent request is a FALSE record);
  wide notes recorded once per origin

### The review fixes
- surface_expand: sibling enumeration (the missed /modals/* pivot)
- SURFACE STALL: same-surface-different-args grinding (≥4 attempts, no
  growth) fires the forced-pivot directive
- XSS impact-exploration playbooks: OAuth chaining off mapped
  state/redirect_uri, cookie/token exfil, authenticated reads, CSRF
  harvest — plus browser-verification for execution claims
- Doctrine: FILTERED ≠ SAFE (4-5 distinct variations, one axis at a
  time) · EVIDENCE OR IT DIDN'T HAPPEN (the diff, 403 = enforcement
  works)

### No-orphan-code, enforced
`suijin capability` audit verb + CI gate: routes ↔ catalog parity +
pack integrity. Orphans are build failures, not archaeology. The dead
testssl/wafw00f packs are fixed; 301 routes at full parity.

## v6.5.0 — The Weaponization Engine

The agent stops being a recon buddy. Everything in this release exists
to make exploitation autonomous, deep, and honest about it.

### The posture (not-pussy doctrine)
- **Recon targets, exploitation executes.** The old default skill
  taught "NEVER run exploits until 3+ recon steps" every single turn;
  the engagement order ended in caution ceremony; the RULES block was
  100% prohibitions. All inverted — procedural inevitability framing
  (a form seen is a form tested), iteration licensed ("probe → adjust
  → fire again is the workflow, not a stall"), authorization is
  settled procedure, "a vulnerability confirmed but unexploited is
  work unfinished."
- **posture dial** (`assertive` | `recon`) in config/Settings.

### The mode machinery
- **Attack Surface Queue**: every recon artifact enqueues as untried
  attack debt, visible in context every turn with the scoreboard
  (findings · untried · mode · FOOTHOLD).
- **Mode governor**: recon stalls or dries → forced switch to
  exploitation with the best-fit doctrine (the skill-stuck bug is
  dead). Foothold detected (shell output, cred capture, shell-class
  CONFIRMED) → forced post_exploitation with the privesc/pivot/loot
  doctrine. The supervisor can no longer say "generate report and
  complete" while untried surfaces remain.
- **Oracle actuation**: anomaly hypotheses auto-fire ONE validation
  probe (paced) — evidence, not homework.

### Weaponizer + positive memory + chains
- **ESCALATION READY** on every catalog_exploit CONFIRMED: a 15-class
  playbook (sqli→extraction discipline, cmdi→shell, ssrf→metadata
  sweep, upload→webshell, jwt→forge+admin…) builds the ready
  deploy_subagent task — one decision, the team deepens in parallel.
- **what_worked**: the first POSITIVE memory reader — prior CONFIRMED
  exploits for this target + class-level transfer from matching
  stacks. Memory keys by host, not objective prose.
- **CHAINS READY**: deterministic ingredient rules (creds×login →
  replay; JWT×admin → forge; upload → blocklist bypass; SSRF →
  metadata; foothold → privesc).

### Provider fleet — 6 → 24
- 13 cloud registry rows (OpenRouter aggregator = one key for every
  major model, OpenAI, xAI, Mistral, Groq, Together, Fireworks,
  DeepInfra, Cerebras, SambaNova, Perplexity, Cohere, Lambda) — each
  activates the moment its key lands in .env.
- 5 keyless local (Ollama, LM Studio, vLLM, llama.cpp, Jan) — never
  priced, the governor can't stop on free.
- **custom: LAN boxes** — `custom_providers` in config.json points at
  any OpenAI-compatible server at any IP:port.
- **Provider self-healing**: credit death auto-falls-through to the
  next key-bearing provider + local Ollama instead of dying.
- Root-cause fix: partial configs silently routed every red call to
  deepseek while config said zai (the recurring 402).

### Self-service
- **adjust_config**: the agent tunes its own run (posture,
  temperature, tokens, provider, fallback chain) at both live seams;
  cost caps/stealth/safety/scope are structurally operator-only.
- **write_tool** fixed: writes real loadable packs (immediately
  routable), not dead files.
- Missing-binary tool errors now carry this OS's exact install command.

### New tools
- **bypass_403**: ~24-variant battery (path normalization, header
  injection, method overrides) through paced http_request with a
  verdict table.
- **code_harness**: the exploit dev loop — write→run→triage→fix in a
  sandbox; VERDICT PASS is the finding evidence (closes AI_CLAIMED).
- **payload_mutate**: evasion variants + family-escalation ladders
  (reflected → blind → time-based → OOB) for blocked payloads.

### CITADEL — the insane lab
Armored fortress (WAF fake-404s, rate limits, decoy admin, fake .git,
deceptive metadata, decoy flag that validates false). 26 planted
vulns, three crown chains (SQLi→RCE root flag, SSRF→rotating vault→
command-injection decryptor, race→executive ATO), 22 chain-proof
tests, bench-graded. The gym loop feeds bench misses back into the
next run's context.

### Fixes the field demanded
- Crash errors ALWAYS visible (the queue-bridge reader swallowed
  them; the panel survived the screen clear; press-Enter pause).
- 402 instant-kill with an honest banner naming the provider.
- Resume honors CURRENT config (stale .sje configs no longer override
  provider switches).
- M-token display (2.40M, not 2,400k). Uncapped flexing input box.
- Browser snapshots fixed (the fold-clip rejected everything below
  800px; SPA hydration grace added) — live-verified end to end.
- Ask-operator window shows the input box while you type; creds_add
  tolerates argument drift (note/type/token shorthands).
- read_file/write_file: tilde expansion, never-raise path errors.
- Infinite iterations + all cost enforcement 0=disabled everywhere.
- Housekeeping: desktop app retired, treeaudit module committed,
  .gitignore hardened. 1,996 tests green.

## v5.7.0 — The Interactive Operator

The operator and the AI are in a live conversation: you type, the AI
reads, a green panel confirms delivery. The stream is clean. Bugs are
reproduced in a real PTY before they're fixed.

- **File-based live guidance**: the input box writes to
  live_guidance.md; the think node reads it at the TOP of the system
  prompt (above doctrine); a green panel confirms delivery; the
  context manifest shows exactly what the AI was fed each turn. Zero
  LangGraph state mutation — a file read is atomic and cannot fail.
- **Adaptive typewriter stream**: 60 micro-increment gears (20-3000
  chars/sec); think = light, speak = cyan; command boxes; duplicate
  span dedup; model newlines are paragraph breaks; 15s progress
  indicator (deduped, suppressed during pause).
- **Classed vulnerability registration**: agent-defined severity +
  CVSS display prominently in the TUI; POC step-scripts the TERMINAL
  executes; three-choice failure flow (edit / abandon / claim-anyway
  with amber AI_CLAIMED stamping).
- **Per-engagement state**: schema/recovery/scratchpad/approvals scoped
  to outputs/engagements/<slug>/; ended runs archive automatically;
  recovery refuses garbage objectives; scratchpad [operator] tags are
  [guidance-memory].
- **.sje bundles**: hash-sealed engagement snapshots; `suijin load`
  resumes with full memory.
- **Interactive review rig** (scripts/tui_drive.py): real PTY, raw
  keystrokes, real frames; four field bugs caught live.
- **Diagnostic logging** (outputs/logs/diag.log): every LLM call, tool
  call, node transition — JSONL, rotates at 10MB.
- **Always-at-the-bottom input box**: mode cycling (Tab), model
  intelligence (Alt+I), instant ESC ESC pause, /quit full-save.
- **Ask-operator typing fixed**: answers route through the reader's
  ask queue (console.input fought the cbreak reader).
- **Timeouts tightened**: 120s read (was 300s), 180s hard cap (was
  600s) — invisible 10-minute stalls killed.
- **kb_read tiered path matching**: exact -> suffix -> prefix ->
  substring (the SQL/NoSQL confusion is dead).
- **Module-pack import fixes**: knowledge_graph, fileio, search_cve
  (legacy path resolution).

## v5.6.1 — Installability Fix

`pip install suijin` works on a clean machine; the container boots,
takes verbs, and passes its own healthcheck; uninstallable metadata is
now a build failure instead of a user's error report.

- **pip ResolutionImpossible fixed**: the 5.6.0 wheel declared
  `rich>=13,<14` alongside `textual>=8` (which requires `rich>=14.2`) —
  an unsatisfiable pair; every fresh install died. Bounds are now
  `rich>=13,<16` (pyproject + requirements.txt), and textual ships in
  the container so the Module Manager TUI opens there. Dev machines
  never saw it: pip does not re-check installed packages against new
  bounds.
- **Container crash fixed**: the entrypoint imported `suijin` before
  putting its parent on `sys.path` — `ModuleNotFoundError` under
  `docker run` (WORKDIR is `/app/suijin`, the package root `/app` was
  never on the path). Bootstrap now runs before all imports; known CLI
  verbs (`version`, `doctor`, ...) dispatch headless from the
  entrypoint while pytest-style argv never dispatches (`is_known_verb`
  gate, pinned to the real argparse choices by test so verbs cannot
  drift).
- **Image healthcheck fixed**: doctor's required binaries included
  `feroxbuster` and `john`, absent from the curated apt list — the
  shipped image marked itself unhealthy on first boot. Both installed.
- **Blue console polish**: heartbeat strip thread (uptime ticks,
  spinner never freezes between requests), always-visible input
  affordance row (`» /block <ip> · /state · /shell <cmd>`), one-line
  baseline training progress (no more banner spam, duplicate verdict
  removed).
- **Publishing hardened**: the wheel is built, installed into a clean
  venv, and the CLI run — on every push and PR — and `pypi-publish`
  waits on that proof; the 5.6.0 metadata bug class is now caught
  before upload.

## v5.6.0 — The Hill CTF and Blue Freedom

The blue team stops being a classifier and becomes a defender; a new CTF
lab gives both sides a proving ground; first registry publishing.

- **The Hill CTF** (lab/hill_ctf, 7 files): four guarded perimeters —
  decoy perimeter (admin-panel/git/robots bait, decoy token), JWT
  two-step + IDOR foothold (fragments + canary creds in docs), SSRF
  pivot through internal metadata (canary creds get DECEIVED with a
  fake keypair + critical trip), command-injection vault with a
  15-minute rotating token and a force-rotate lever. Typed severity-
  tagged events (hill_events.jsonl), standard traffic JSONL, and
  hill_defense.json levers (login rate limit, SSRF blocklist, decoy
  sensitivity, force-rotate). 24 tests incl. both JWT forgery paths,
  canary trips, the full chain walk, rotation invalidation.
- **BF0 honesty**: zero-defense verdicts impossible (REVIEW/LOG/unknown
  -> fallback tarpit), honest detected/tarpitted/blocked/deceived
  counters, prefix-boundary fix, per-instance state. 10 tests.
- **BF1 arsenal + enforcement plane**: defenses serve AT THE PROXY —
  blocks (403), honeypots (crafted content instead of forwarding),
  fake responses, per-IP redirects, canary tripwire with recorded
  hits. Namespaced blue tool registry (11 tools; gated blue_shell with
  red's guardrails) deliberately NOT kernel-registered — no red/blue
  prompt leakage in one-process deathmatches (tested gate). The Hill
  boots behind the proxy from the blueteamer menu. 18 tests, all with
  observable proxy effects against the live lab.
- **BF2 org chart**: the long blue prompt (doctrine, escalation policy
  per attack class, 8 defensive playbooks, creative-scripting
  freedom), defensive orders, the _blue_mode seam in think_node, zero-
  LLM per-endpoint watchers (auto fast-path: tarpit+block instantly on
  critical hits, analysis finally seeds them), blue-routed incident
  responders via the fireteam mechanics (prompt advertises the BLUE
  arsenal — the audit's coupling fixed). 12 tests to the proof
  standard (no red leakage; scripted episodes land real enforcement).
- **BF3 live console**: event blocks (request -> verdict -> action,
  syntax-highlighted commands), pinned strip (req/threats/blocked/
  deceived/uptime, spinner), always-active input box (/block /unblock
  /state /tarpits /canaries /report /rotate /quit + free-form shell).
  15 tests.
- **Publishing**: publish.yml — GHCR multi-arch image (GITHUB_TOKEN,
  zero new credentials) + PyPI wheel (PYPI_API_TOKEN secret) on v*
  tags, PR-side build-only proof jobs, GitHub Release with the
  release-notes body. compose pulls ghcr.io/0xwi11iam/suijin:latest;
  Dockerfile version label from build arg; README quickstarts for both
  registries.

59 new tests since v5.5.0. Full suite 1,727 fast + 6 slow green; ruff
clean.

## v5.5.0 — Harness competence

Six evidence-backed repairs to the decision loop — not new tools, not
benchmark tuning (no bench/lab was touched): the brain the tools run on.

- **H1 engagement state board**: target_info finally populated from tool
  outputs (nmap ports/services, header + bundle endpoints/tech,
  credentials, subdomains), rendered every turn with the tested-axes
  coverage map and running background jobs; the fake growth detector
  (a dict compared against itself, always False) replaced with the
  honest execute-set flag the stall counter reads.
- **H2 job semantics**: finished background jobs drain into the
  conversation at think time (fireteam symmetry — a field run's leaked-
  key scan was never collected); job_wait/status/output/list exempt from
  the 10s auto-background promotion (a wait promoting itself into a job
  made results uncollectable); job_status untruncated for finished jobs.
- **H3 dispatch anti-repeat**: three identical failures of the exact
  same call hard-block with the last error + named alternatives
  (prompts alone demonstrably fail — one call repeated 80x/9.5h);
  payload iteration always allowed; chain_failures_memory finally
  written (initialized, read, never written since inception); latent
  fix — HTTP failures recorded as successes in the trace.
- **H4 control plane**: switch_skill + plan_tools advertised with
  concrete shapes (51 skills, zero switches ever recorded before);
  _plan_remaining moved top-level (was nested where execute dropped it
  — plans lost steps 2..N at that exact line), rendered as a QUEUED PLAN
  block, drained as heads execute, cleared on course change; todos
  render IDs; two prompt-hygiene CI gates (background-section tools and
  curated-registry entries must exist in the booted route table).
- **H5 claim-time verification + memory repair**: record_finding grades
  every claim immediately (verdict rides the result line); recipes 5→10
  classes + alias map; the keyword-mention 'verified' fallback killed;
  memory repaired at three joints (note() arity TypeError swallowed —
  zero memory across 361 sessions; recall rendered at start;
  record_engagement at end); scratchpad duplicate-burst suppression
  (operator noise once hijacked a run's priorities).
- **H6 telemetry**: audit rows carry their iteration (every row in
  every agent_steps.jsonl read 'iteration=?').

51 new tests across the waves. 1,648 fast + 6 slow green; ruff clean.

## v5.4.0 — The capability ecosystem

Person-to-person capability distribution: single sealed files with
attribution, a safety scan and a review wizard before any code runs.

- **`.sjm` / `.sja` / `.sjp` packages** — module packs, addons and
  tier-gated kernel plugins, one container spec (sjpkg.json +
  SHA256SUMS + payload; format v1; zero new dependencies). `suijin pack
  build` auto-extracts the tool table from code and auto-fills invalid
  manifests; `suijin install` runs the wizard (attribution, dev note,
  scan verdict, tools table, external binaries) — Enter installs.
- **Built-in AST-only safety scanner** (platform/lib/safety) — never
  executes payload code (no-side-effects test); critical: hidden
  eval/exec, string-multiplication obfuscation blobs, hardcoded secrets
  in source assignments, built-in tool shadowing; warnings: undeclared
  spawns, network egress, import-time effects; declared binaries are
  honest metadata. Install ALWAYS re-scans — embedded reports advisory.
- **Guards, each tested**: tamper (names the file), path traversal,
  symlink entries, zip bombs, tool-shadowing refusal (the loader's flat
  namespace makes a shadow a supply-chain takeover), core-tier plugin
  refusal.
- **Malicious examples shipped** (examples/malicious/, 5 packs): the
  fixtures exposed and fixed two real scanner gaps; CI builds each and
  asserts refusal. Clean examples sealed in examples/built/.
- **Field fixes**: spinner at 3x default frame rate (Rich speed
  semantics were backwards — earlier values slowed it), RunBox shares
  the engagement console (mid-refresh interleaving), core_utils
  search_kb accepts its documented limit kwarg, wizard shows the outer
  sha256 for comparison against the author's published hash.
- docs/sjpkg-spec.md — author guide + the rent-ledger deletion
  criterion; README command-table rows; plan.md untracked (operator-
  local) along with engagement notes, KG store and crash logs.

37 new sjpack/scanner tests; 1,597 fast + 6 slow green; ruff clean.

## v5.3.0 — Field-hardened

Every change in this release came out of live field engagements (target
names withheld) — each field run's bug report drove a fix, and the
crash-log system introduced here makes the next ones diagnosable in
seconds.

### Engagement console UI (rebuilt from field runs)
- **One iteration = one rendered block**: rule-delimited sections —
  thinking (dim blue), said (cyan), the command in a syntax-highlighted
  mini-terminal (bash/json/python/markdown per tool), output rendered as
  markdown or syntax-highlighted by content type. No truncation anywhere.
- **Fast thinking spinner**: the pinned strip doubles as the spinner
  (20fps, npm-snappy dots) showing phase · iteration · tokens · cost ·
  FLAG/CRED/FT counters; `~` marks estimated pricing.
- **Uncrashable renderer**: every render method is guarded — a render
  bug logs to `outputs/logs/ui_crash.log`, falls back to plain text, and
  the engagement keeps running. The loop itself logs
  `engage_crash.log`. Both already caught real field bugs.
- **No silent endings**: one classifier, one banner per ending —
  COMPLETE (green) / DECLINED (yellow, with the authorize fix) /
  OPERATOR STOP / FAILED (red + last model output) / FAILED — NO OUTPUT
  (provider hint).
- **Instant Ctrl+C**: SIGINT raises in place; the pause menu appears
  immediately instead of waiting out the current LLM call.
- **15-command pause console**: `/objective` `/phase` `/focus` `/skip`
  `/finish` `/loot` `/jobs` `/kill` `/cost` `/report` `/audit` `/state`
  `/sessions` `/template` `/health` — course changes mutate graph state;
  extracted to session_control so every command is tested.
- ask-operator flow: the question renders as dim markdown, the prompt is
  exactly `Answer:`, the live strip stops during input (it used to eat
  the prompt), and scope-doubt questions auto-answer from the
  authorization record.

### Operator authorization workflow
- **`suijin authorize <domain>`**: attestation ledger — program, auth id,
  90-day expiry, subdomain coverage; renders in EVERY engagement order so
  the agent never re-litigates it.
- **`suijin bb-scope <program-url>`** (advisory): pulls real program
  scope via bugscope (5 platforms, operator token per-call, never
  stored); in-scope guides targeting, out-of-scope steers away; the
  agent `scope_search`es the cache live.
- **`fetch_authorization_page`** tool: eyes-on verification of the
  program page with the explicit verdict doctrine — a Cloudflare/WAF
  block means the page EXISTS (nonexistent pages 404); that is ample.
  Operator answers containing a URL persist onto the record.
- Framing rebuilt calm after field feedback: force-language (VERIFIED &
  SECURE, never-question, statute cites) primed meta-suspicion in
  capable models; a flat procedural record gets treated as settled fact.

### Supervisor: battle-buddy, not interferer
- 4 interference bugs fixed (phantom-tool verification demands,
  thought-text false findings, research counted as bookkeeping, payload
  iteration counted as no-progress).
- **52 tactical follow-up heuristics**: signal-seen + follow-up-missing
  → one concrete hint (JWT → jwt_inspect, SQLi confirmed → sqlmap
  extraction, AIza key → google_key_probe, foothold → privesc basics,
  403 wall → verb tampering, …). Cooldown-gated, only when pathology is
  silent.
- Per-detector cooldowns; LLM deep analysis every 15th iteration; oracle
  scoped to http_request response triage only.

### Cost & provider reliability
- Per-model pricing completed (glm-4.6, Qwen3-Coder, case-insensitive
  matching); the dead estimate path fixed (unbound variable silently
  recorded zero); estimate fallbacks for every provider; lobstertrap
  counted; `priced` flag honest.
- Provider retry noise silenced (no more raw `Z.ai attempt N failed`
  walls); provider_failure gets ONE automatic full restart before the
  engagement ends; parse_failure banners explain themselves.
- Fireteams: 60s configurable LLM timeout + patient retry (the 15s
  double-kill from field notes is dead).

### New tooling
- `js_bundle_analyze` / `google_key_probe` / `source_map_probe` —
  SPA attack-surface mining in one call (built from a field run where
  the agent hand-rolled three iterations of broken curl+grep).
- Every core tool (55) now has a dedicated console render (content
  tools show content, no-arg tools show no block) and is TAUGHT in the
  prompt registry.
- mcp_playwright hardened: missing-dependency fails fast with install
  instructions (was a 30s hang + false "Browser timeout"), generation
  counter kills stale results, `domcontentloaded` default (networkidle
  never fires on analytics SPAs).

### Durability
- **Reinstall-durable workspace**: state lives at `~/.suijin/workspace`
  (outside any repo copy) — sessions, memory, the authorization ledger,
  bugscope pulls and reports survive re-clones and reinstalls.
  `install.sh` migrates legacy + Medusa-era content into it and symlinks
  the repo-local path; `SUIJIN_WORKSPACE` overrides.
- Interactive installer asks install-type FIRST (normal vs dev; dev is
  the default inside a checkout, live symlink, `--dev[=PATH]` for
  non-TTY).

1,552 fast + 6 slow tests (110+ new since v5.2.0); ruff clean; boot
150 units / 265+ tools / 140 packs.

## v5.2.0 — Field readiness

Five waves since v5.1.0, all driven by what live engagements actually
hit (the field-target/prior-target notes) and what offline measurement proved.

### Red-team console UI
- **Engagement transcript rebuilt** (`red/console_ui.py`): per-iteration
  blocks — thinking line, `:: why ::` reasoning (hidden by default,
  `/think` toggles + re-prints the last one), `> tool` with per-tool
  syntax highlighting (bash/json/python/markdown), boxed output,
  colored loot lines (FLAG{...} gold, credentials green — 8 cred
  patterns), supervisor/oracle/drift/fireteam/phase renders.
- **Pinned live strip** (battle.py pattern): iteration · phase ·
  tokens · cost · FLAG/CRED/FT counters; `~` marks approximate cost.
- The prompt now requests optional `reasoning` (1-2 sentences WHY).

### Truthful events (bugs the UI exposed)
- Stale `_current_step` re-executed the previous tool after
  transition/ask_user/switch_skill/complete turns — cleared.
- Trace `success`/`error_class`/`tool_output` never back-filled from
  execution (the `!` failure marker could never fire; supervisor
  dead-end detection was blind) — execute events now replace the
  trace entry by iteration.
- Audit observations logged empty every normal iteration (think-side
  raced ahead of execution) — now logged from execute events.
- BLOCKED rendering keyed on real policy/not-found prefixes (the old
  check was unreachable); ask_operator prints once.

### Accurate token cost (per model)
- `record_missing_usage` referenced an unbound variable in the
  amd/deepseek/zai branches — the estimate path was dead code and
  silently recorded ZERO; fixed. Estimate fallback added for
  gemini/huggingface/anthropic; LobsterTrap proxy calls counted.
- Pricing table: +glm-4.6, +Qwen/Qwen3-Coder-480B, +zai-org/GLM-5.1;
  case-insensitive matching (the default HF model previously fell to
  the fallback rate). `priced` gets its documented semantics.

### Fireteams
- Subagent LLM timeout 30s -> 60s (configurable: env
  `SUIJIN_SUBAGENT_LLM_TIMEOUT` / config `subagent_llm_timeout_s`),
  one patient retry before a timeout counts — the 15s-timeout
  double-kill observed in field notes. Deploy confirmations and
  FIRETEAM results now render in the console.

### Blue foundations (wave A — SOC-in-a-box groundwork)
- Tarpit file protocol unified: the battle watchdog wrote `set_at`
  while every reader reads `since` — scripted-battle tarpits moved
  the scoreboard but never delayed red. One protocol module now
  serves battle, TUI feed, and proxy.
- 9 attack classes that only existed in the TUI fast path ported to
  the core detector (command injection, JWT, deserialization, LDAP,
  NoSQL, mass assignment, file inclusion, GraphQL, brute-force UA)
  plus blind SQLi — offline metrics no longer overstate stealth.
- Custom detector rules (`suijin rules`) wired into the production
  scorer + TUI tier (validated-and-ignored before).
- Scorer weights + thresholds driven by `blue_config.json` (was
  hardcoded theater); latent DEFAULT-poisoning bug in
  `load_blue_config` fixed (shallow copy).
- Blueteamer built-in lab launch fixed (BASE_DIR pointed into
  modules/blueteam where nothing exists).

### Graded lab benchmark (wave 5)
- **`suijin bench`**: boots each lab (log4shell/wordpress/oauth), runs
  the agent through real dispatch, scores flag capture / tool calls /
  iterations / cost; history persists for release-over-release
  trends. Mock mode is offline-deterministic; scripts never embed
  flag values (anti-cheat enforced by tests) and thread dynamic
  tokens from tool output.
- oauth lab flaw 6 made genuinely exploitable (password grant now
  honors requested scope — bob's flag was unreachable).

### New attack surfaces (wave 3) + cleanup (wave 4)
- **payloadforge** pack: rev_shell (bash/python/nc/php/powershell),
  encode_chain (b64 → gzip+b64 round-trip), stager (curl/wget/python).
- **containerbreak** pack: docker_analyze (Docker API escape probe),
  escape_check (self-analysis: cgroup/CapEff/mounts).
- wifi `sudo_available`; dead code deleted (RECON_PROFILES,
  attack_simulator, build/), latent endswith/format bugs fixed.

### Installer
- First question is now install type: **normal** (released) or
  **dev** (local source tree, live symlink). Run from inside a
  checkout and dev is the default; `--dev[=PATH]` forces it
  non-interactively.

1,377 fast tests + 6 slow (83 new since v5.1.0); ruff clean; boot
150 units / 265+ tools / 140 packs.

## v5.1.0 — Desktop (technical preview)

The gateway + Tauri client: the agent gets a GUI without the GUI
containing any agent code.

- **Gateway module** (`suijin gateway`): typed FastAPI surface over
  the kernel ctx — status/tools/usage/findings/spar reads, HITL
  approvals + ask-operator writes (the ONLY writes), detached
  engagement launch, and a WebSocket live stream (audit-trail tail,
  cost ticker, HITL snapshots). Per-boot bearer token, localhost by
  default. /openapi.json is the contract.
- **Desktop app** (`desktop/`): React+TS+Vite + Tauri shell.
  Approvals inbox (approve/deny on a live blocked agent), ask-operator
  answers, live engagement stream + launcher, dashboard (units/tools/
  KB/posture, spend with api-vs-estimated accuracy, arsenal-by-owner,
  KG targets). Design system: cold cyan on near-black; Geist /
  Instrument Serif / Fira Code (OFL via Fontsource); icons generated
  from the project logo. TS types generated from the gateway's
  OpenAPI — zero drift by construction.

- **HITL bridge made real**: gateway approvals/questions now read-write
  the agent's actual stores — approving on the desktop unblocks the
  running agent; ask_operator from detached engagements lands on the
  Approvals screen and polls for the operator's answer (10m).
- **Keyboard**: 1-3 tabs, A/D decide the top pending approval.

1,251 tests (10 gateway + 6 bridge); desktop builds clean under strict TS.

## v5.0.1 — STABLE

The stabilization release. Architecture frozen in green; everything
since v5.0.0 is hardening + capability, zero churn.

- **Stealth mode** — ON by default, zero performance loss: sticky
  realistic browser identity (UA-hopping is itself a tell), burst-
  limited pacing (manual probes pay 0s), tool-level rate caps for
  loud tools; 33 packs swept clean of scanner UAs. Env kill switch:
  SUIJIN_STEALTH=off.
- **Agent competence rebuild** — coding-agent-simple decision loop
  (4-field JSON, tolerant nested parser, harness-computed
  productivity); fireteam sub-agents rebuilt (12 steps, per-agent
  budgets, full tool visibility, usefulness gates: vague/duplicate
  tasks rejected, findings compressed to evidence, non-blocking
  teams); scratchpad + structured RESULT observations.
- **Kernel-rendered capability surface** — the kernel renders its live
  tool registry into the agent prompt (262->265 tools with args in
  ~6.4k tokens, was 28k); zero-invisible parity enforced in both
  booted and pre-boot modes; 10k prompt budget gate.
- **REAL battle** — the actual agent graph attacks the live vulnerable
  lab; blue recall scored against ground truth derived from red's own
  requests; verdict persisted. `suijin battle --real|--mock`.
- **Genuine token counter** — APIs omitting usage counted ZERO before;
  now client-estimated (word/CJK-aware) and attributed;
  `suijin tokens` shows the honest tally per provider.
- **bugscope** — 5-platform bug-bounty scope scraper (bbscope method,
  SSL-verified, adapters cross-checked against the reference source).
- **Neo4j-ready KG** — same API, one config switch, fail-safe to
  JSON; blue bridge/dossier/export routed through the public API (the
  bridge was dead code even on JSON — fixed).
- **Local == CI** — scripts/verify-ci.sh replays GitHub's exact steps
  on a fresh worktree + clean venv; three incident classes
  mechanized away.
- Ctrl-C clean everywhere; tool-not-found asks the operator.

1,235 fast tests / 1,238 collected. Boot: 150 units / 265 tools /
136 packs, 0 skipped.

## v5.0.0 — Wave 6: the marketplace + learning loop — ALL 50 FEATURES COMPLETE

The 50-feature roadmap is finished. Major version earned by the
ecosystem:

- **Marketplace (F41-F43)**: decentralized pack indexes — any URL
  serves one; `suijin market search|install|update|list`. Installs
  are hash-pinned (mismatch = refuse), updates roll back on failure.
- **MCP mirror expansion (F46)**: typed input schemas from pack
  manifests for every tool (no more generic args blobs).
- **Self-promoted learnings (G47)**: critique tactics drafted as
  dormant skill files for operator review.
- **Skill decay pruning (G48)**: engagement-history overlap analysis.
- **KG visualization (G49)**: mermaid export of the knowledge graph.
- **Dead-drop exfil detection (G50)**: DNS-tunnel entropy + beacon
  periodicity.

1,160 tests.

## v4.8.0 — Wave 5: blue team depth

- **Attack replay into blue**: red traces score against the real
  detector — training/eval without a live lab.
- **Deception effectiveness** metrics per battle.
- **SOC playbooks**: detections fire registered response actions.
- **FP feedback loop** + allowlist manager.
- **Incident timeline** generation.
- **nginx/Apache log adapters**.
- **Canary tripwires**: credential generation + reuse watch.

1,151 tests.

## v4.7.0 — Wave 4: reporting & workflow

- **Report templates**: exec / technical / compliance views of one
  finding set; en/de/fr/es rendering presets.
- **Engagement templates** + scheduling (idempotent crontab entries —
  the system scheduler drives the CLI, no daemon).
- **Webhook notifications** (Slack/Discord/generic JSON).
- **Client portal**: self-contained offline HTML evidence bundle.
- **Session time-travel**: fork any engagement at iteration N.
- **Session theater**: animated ASCII replay.

1,144 tests.

## v4.6.0 — Wave 3: knowledge & memory

- **Engagement memory**: per-target operational history (what was
  tried, operator notes) recalled at engagement start.
- **Target delta / drift**: fingerprint history with change detection.
- **Evidence vault**: hash-chained, tamper-evident evidence per finding.
- **Finding dedup**: same-root-cause collapse with occurrences.
- **Attack-path scoring**: probability-weighted chains, full-chain
  headline.
- **CVE -> tool advisor** + **KB freshness** prompts.

1,131 tests.

## v4.5.0 — Wave 2: agent intelligence

- **Finding verifier**: independent second evidence path per finding
  (artifact-match = verified; contradictions = dismissed) before the
  report is written.
- **Peer review**: hostile-reviewer + judge LLM passes — keep /
  downgrade / dismiss with reasons; no-LLM degrades gracefully.
- **Context compaction**: deterministic digest of old history past
  ~30k tokens (failures marked do-not-repeat), tail preserved — long
  engagements survive.
- **Dead-end detector**: same tool failing with varying args forces a
  strategy-CLASS switch.
- **Payload-class escalation**: reflected -> blind -> timing/OOB ladder
  hinted from failed-arg families.
- **Confidence tagging**: every step carries verified/probable/
  suspected (unclaimed = probable — never verified without proof).
- **Adversary profiles**: stealth_apt / script_kiddie / insider
  personas change tool preference, pacing, and noise posture.

1,122 tests.

## v4.4.0 — Wave 1: cost control, recipes, planning, author tooling

**Status: stable and ready.** 1,104 tests, 149-unit / 256-tool boot,
10 features (the first wave of the 50-feature roadmap).

- **Cost governor**: hard per-engagement budget kill switch
  (max_cost_usd) checked before every LLM call — over-budget runs end
  with completion_reason=budget_exhausted; warn path steers the agent
  to wrap up; unpriced tallies never brick a run.
- **Efficiency leaderboard + forecast**: findings-per-dollar per
  engagement + per-action cost projections in `suijin status`.
- **Failover telemetry**: provider chain outcomes (primary-ok /
  failovers / all-down / last event) in `suijin doctor`.
- **Prompt budget profiler**: per-iteration token breakdown (system vs
  history) trended in sessions; `suijin profile` renders the latest.
- **Tool recipes**: named multi-tool macros (built-ins: recon_web,
  subdomain_sweep, email_recon) with {target}/{prev} chaining; agent
  tools recipe_run/list/define + `suijin recipes`.
- **Auto-recipe miner**: `suijin recipes mine` n-gram-mines repeated
  successful sequences from engagement history into adoptable macros.
- **Objective decomposer**: `suijin plan` — LLM subtask graph with
  heuristic fallback (recon->enumerate->test->verify->report spine).
- **Plugin test harness**: `suijin module test <name>` — files, schema,
  boot, registration, catalog, callable smokes; caught its first real
  gap (encodesk docstrings) within minutes of existing.
- **Wordlist hub**: curated SecLists subsets fetched sha256-verified
  (`suijin wordlist list|fetch` + agent tools); mismatches rejected,
  never fed to brute tools unverified.
- Round-3 harness tools (v4.3.x): +30 tools incl. agent-defined
  custom commands (cmdsmith) and inline python (pyrun) — 256 total.

## v4.3.0 — harness round 2 (+51 tools), self-critique, sparring, turnkey deploy

**Status: stable and ready.** 1,053 tests, 137-unit / 221-tool boot
(0 skipped), four install paths, offline-verifiable everywhere.

### Harness: +51 tools across 39 packs (221 total)
Cloud (IMDS probes, bucket checks, aws/gcp/az wrappers), AD/kerberos
(kerberos hash formats, SPN candidates), API security (GraphQL
introspection + field suggestions, OpenAPI spec hunting + parsing,
mass-assignment + verb tampering, gRPC detection), mobile statics
(APK inventory + secret strings, IPA Info.plist), secrets (regex +
entropy scanning, source-map exposure, Terraform/Dockerfile lints),
OSINT (CT logs, DoH + split-horizon compare, GitHub dorks, raw WHOIS,
email harvest), web depth probes (SSRF canaries, open-redirect
validation, Host-header injection, cache poisoning pre-checks,
takeover fingerprinting, backup hunting), infra (offline MAC vendor
table, NVD CVE search, stdlib TCP scan, DoH subdomain brute).
Every tool offline-tested; catalog parity holds.

### Self-critique
The agent grades its own engagement post-run: what worked, what
wasted calls, missed leads, tactics to remember — report saved to
outputs/reports/critique_*.md and tactics recorded to the knowledge
graph for future engagements. Config-gated, never fatal.

### Sparring mode
`suijin spar`: fixed practice volley through the REAL blue detector,
scored against stored baselines with regression detection and CI-gate
exit codes. Detector drift becomes visible the moment it happens.

### Deploy: four turnkey paths
- macOS/Linux native: install.sh — interactive start (OS + pip
  preference with detected defaults), FULL dependency resolution
  (git/python3/venv/build headers auto-installed, stale-venv rebuild)
- Windows: install.ps1 (Docker-only by policy; native refused with a
  clear pointer)
- Docker: docker.sh + .env.example (one command; colima + Desktop),
  minimal-footprint image (kali-linux-core, opt-in heavyweights)
- Existing Kali container: kali-setup.sh (curl-pipe, full arsenal,
  hard-stops on non-Kali)

Also: mcp_server sys.path bootstrap bug fixed (surfaced when the venv
was rebuilt); 7 vacuous test skips in test_graph.py became 11 live
behavioral tests; install e2e re-verified against every installer
change.

## v4.2.0 — audit trail, outputs consolidation, skills & addons; turnkey deploy

**Status: stable and ready.** 1,021 tests, 98-unit / 172-tool default
boot (0 skipped), Docker + installer overhauled, docs rebuilt with a
developer guide.

### Audit trail v2
Every tool invocation on every surface is recorded append-only under
`outputs/audit_trails/` (tool_calls / agent_steps / cli_calls JSONL).
Arg VALUES are never stored — key names + sha256 digest only, so
secrets can never leak into the audit log. The kernel choke point
(Context.call_tool) covers red, blue, MCP, and all packs; the agent
loop and CLI verbs log their own streams.

### Outputs consolidation
ALL engagement artifacts nest under `suijin_agent/outputs/` (reports,
dossiers, exports, sessions, audit_trails, blue_state,
compliance_reports, wordlists, payloads, sandbox). One-time idempotent
migration moves existing dirs; state/config stays at the workspace
root. In Docker the workspace is a named volume — a rebuilt container
picks up exactly where the last one left off.

### Skills — drop-in markdown (new module)
`suijin/skills/*.md` boots into the agent's system prompt (8KB/file,
64KB budget, `<!-- skip` dormancy). No manifest, no code. Boot-gated:
drop a file, build the prompt, assert it's there.

### Addons — zero-boilerplate tools (new module)
`suijin/addons/<name>/main.py`: every public callable becomes an agent
tool at boot (docstring -> description, signature -> parameters,
manifest synthesized in memory). Reachable through dispatch AND the
kernel; catalog parity enforced. `suijin module adopt <name>` graduates
an addon into a full pack.

### Web UI removed
The local web dashboard (React source + node_modules + dist + Flask
server + `suijin ui`) is gone per operator decision. `_enrich_traffic`
(blue scoring used by `suijin watch`) was preserved in the CLI. Flask
remains a dependency for the lab targets.

### Docker & installer overhaul
- Dockerfile: OCI labels, pip extras baked (impacket/dnsrecon/wafw00f/
  dirsearch/medusa), snmp + redis-tools, HEALTHCHECK (suijin doctor),
  declared workspace VOLUME
- docker-compose: named volume `suijin_workspace` (state survives
  recreation), init:true, healthcheck, ZAI key, read-only config mount
- install.sh tiers: core (default) / `--tools` / `--full` via brew or
  apt, step-counter progress, post-install doctor; e2e-tested

### Docs
New `developer.md` (repo tour, module anatomy, extension ladder,
gates, conventions, release checklist); README + ARCHITECTURE updated
throughout.

## v4.1.0 — STABLE: harness expansion + hardened agent surface

**Status: stable and ready.** 1,026 tests green, 96-unit / 172-tool
default boot (0 skipped, 0 quarantined), every routed tool advertised
to the model (catalog parity is now a CI-enforced contract), Docker /
install / wheel verified against the final tree.

### The agent's ceiling, raised
- **+50 new tools across 35 new module packs** (suijin/modules/*):
  24 pure-Python packs that work on ANY install (encoding/hash-id +
  offline cracking, JWT audit, IP/CIDR math, port/KB, artifact
  extraction, header/cookie/CORS/vhost/timing probes, TLS cert via
  stdlib ssl, Wayback CDX, ASN lookup, robots/sitemap/form/comment
  parsers, payload context-encoders, ROT-N/XOR, dev tooling, wordlist
  IO, UA analysis, password heuristics) and 11 binary-wrapped packs
  availability-gated with install hints (wafw00f, testssl, dirsearch,
  dnsrecon, crackmapexec, impacket, hashcat, medusa, snmpwalk,
  redis-cli). Total: 172 tools.
- **ask_operator parse bug fixed** — the system prompt taught it, the
  graph nodes handled it, the parser alone rejected it: every legal
  ask burned all 3 retries (agent-log evidence). Prompt/parser/node
  agreement now regression-pinned.
- **Catalog parity contract**: a tool the model cannot see is a tool
  that does not exist — every dispatch route must appear in the
  catalog (tested; sole exception deploy_subagent, an action with
  corrective routing).

### Simplifications
- The compiled Rust core (native/) RETIRED: the pure implementation
  was byte-identical and resolves 96 units in milliseconds. Kernel
  suite (180 tests) pins it directly.
- Runtime/operator JSONs out of the package: engagement state,
  blue_config, notify.json live in the workspace (the Docker volume);
  the package ships only version.json + config.json.
- Tests organized per-module (tests/{kernel,architecture,platform,
  agent,tools,knowledge,providers,ops,blueteam,redteam,console}).

## v4.0.0 — The Modularisation (everything is a module)

The strangler-fig refactor completed: ALL code now lives in
`suijin/modules/` under 10 first-party homes + 49 vendored tool packs,
composed only through the kernel. **Breaking (deliberate):** the old
import paths are gone with no shims — `suijin.tools.*`, `suijin.core.*`,
`suijin.helpers`, `suijin.security`, `suijin.infra`, `suijin.nodes`,
`suijin.prompts`, `suijin.skills`, `suijin.intel`, `suijin.kb`, and the
repo-level `Modules/` tree. Runtime data (KB, KEV caches) moved to the
workspace (`suijin_agent/caches/`) — in Docker the KB now survives
container recreation. CLI verbs, entrypoints, Docker and install flows
are unchanged. Kernel hardening: LayeredConfig deep-copy fix (layered
config could be mutated through a shared nested dict), fault-injection
+ property suite.

## [4.0.0] — 2026-08-19 — SUIJIN OS

The architecture release. Suijin is now a modular operating system for
security automation — same product, same commands, same look; the
internals compose like an OS:

- **Kernel** (`suijin/kernel/`, 12 stdlib-only subsystems): contracts,
  context (the syscall table), events (fault-isolated, depth-bounded
  pub/sub), registry, controller (`boot()` scene analysis, quiet-boot),
  jobs, vfs, security, config (deep-merge layers), health, journal
  (atomic drain, counted drops), errors. Purity enforced two ways.
- **Compiled core** (`native/suijin-core`, PyO3 abi3): resolve_dag +
  check_paths — the only two pure data-in/data-out functions crossing
  the boundary. Pure-Python oracles ship as permanent fallback and CI
  asserts canonical equality (fixtures + 600-case fuzz).
- **Three tiers**: core (platform, tools, agent — nested graph/nodes/
  memory, console; boot-required), recommended (providers, redteam,
  blueteam, knowledge, ops + all 49 packs — bundled, disableable),
  installed (community, `~/.suijin/modules/`). 61-module full boot.
- **Module Manager**: Textual TUI + CLI verbs (list/info/enable/
  disable/install/uninstall) over one management API; install refuses
  core imposters, reports python deps with exact pip commands,
  `--with-deps` opt-in.
- **Disable = disappear**, at every tier, end-to-end proven: tools,
  services, AND menu entries never exist on a boot that excluded the
  module.
- **ARCHITECTURE.md** — the OS manual (subsystems, boot sequence, tier
  model, copy-paste module recipe).
- **Boundaries blocking in CI**: kernel purity (AST + clean
  interpreter, path-guarded) + module boundary rule (module-level
  imports: stdlib + kernel only).
- Pre-release audit fixed 7 kernel bugs (see 3.13.0), including a
  vacuously-green purity linter that had hidden two real violations.

Built strangler-fig across v3.3–v3.14 with the suite green at every
step — never a big-bang rewrite.

## [3.13.0] — 2026-08-19 — KERNEL AUDITED

Full pre-assembly audit of every kernel subsystem; 7 real bugs found,
fixed, and pinned by regression tests (test_audit_regressions.py):

1. **The purity linter was vacuously green since Phase 1** — its glob
   path resolved to a nonexistent directory, scanned zero files, and
   passed forever while hiding TWO real violations. Now path-guarded
   (fails if fewer than 6 files found) and violation-proving.
2. **Kernel->modules inversion**: controller imported
   suijin.modules.manager. Dependency-inverted — boot() accepts an
   enabled_check callable; the modules-world injects, the kernel never
   reaches out.
3. **Journal.flush lost entries racing the write**: snapshot-then-clear
   dropped anything appended in between. Now atomic drain (entries
   leave the ring under the lock before the disk write), disk-failure
   REQUEUES the batch (a journal never silently drops on I/O errors),
   and flood displacement is COUNTED (journal.dropped) — the stress
   test proves every entry is on disk, in the ring, or counted.
4. **Stale compiled core after every cargo rebuild**: the native shim
   only copied the dylib when the .so was absent — mtime-fresh now.
5. **Re-entrant event emit blew the stack** (a subscriber emitting its
   own event recursed to RecursionError). Depth-bounded (10) with a
   drop warning; legitimate chains (a->b->c) unaffected.
6. **LayeredConfig shallow merge wiped sibling keys**: a layer
   overriding one key in a nested section erased the section's other
   keys. Deep merge.
7. **VFS rejected its own workspace root**: absolute paths weren't
   symlink-normalized, so on macOS /tmp/ws ≠ resolved /private/tmp/ws.
   Root and trailing-slash spellings allowed; escapes still blocked.

Also: packs are now FULLY self-contained (each generated entry loads
its own manifest.json + main.py by file path; the _packbridge seam is
deleted — boundary test enforces its absence). The tools bridge
excludes every booted pack's declared tools regardless of boot order.

## [3.12.0] — 2026-08-18 — MODULE MANAGER (PHASE 4)

- **Management API** (`suijin/modules/manager.py`): install/uninstall/
  enable/disable/list/info — the single source of truth behind both the
  CLI and the TUI. Installs validate manifests, refuse core-tier
  imposters into user space, protect bundled modules from uninstall,
  and report python deps with the exact pip command (`--with-deps`
  opts in; nothing executes implicitly).
- **Textual Module Manager** (`suijin module`, bare): tier-grouped
  table + detail pane (deps /, permissions, source), keys for
  toggle/install/uninstall/info/deps/perms/boot-report. Driven
  headlessly in tests via Textual's run_test pilot.
- **CLI verbs**: `suijin module list | info <id> | enable <id> |
  disable <id> | install <path> [--with-deps] | uninstall <id>` (plus
  the existing SDK init/validate under the same command).
- **Boot honors enable/disable**: disabled recommended modules are
  dropped before materialization — tools, services, AND menu entries
  never exist that boot (verified end-to-end: disabling redteam +
  blueteam leaves exactly [ops] in the menu). Disabling CORE aborts
  with a readable reason. State is ~/.suijin/modules.json.

## [3.11.0] — 2026-08-18 — PACKS ASCEND (PHASE 3 COMPLETE)

All 49 Modules/ packs are kernel plugins:

- **pack_converter** (`suijin/modules/pack_converter.py`): generates
  plugin.json + entry shims from every legacy manifest — flat layout
  (dest/<id>/, category kept as metadata), ids from directory names,
  permissions derived from declared binaries, collision detection.
- **Pack bridge** (`_packbridge.py`): the ONE seam to the legacy pack
  loader (file-located — suijin/modules is now a package, so
  `suijin.modules.loader` can no longer be imported; the seam is
  isolated and dies in Phase 5).
- **Ownership decided by the BOOT, not ambient discovery**: the legacy
  loader mixes core builtins into its registry (core_utils re-declares
  search_kb/apply_patch/claim_flag), so pack ownership is computed from
  ctx._booted_unit_ids — stamped before start() — and the tools module
  bridges core-only when packs booted, everything when they didn't.
- Verified both modes: pack-less = 12 modules / 124 tools-owned (legacy
  identical); with packs = 61 modules, tools owns 31 core, packs own
  their 93 (nmap owns nmap_scan).
- Full OS boot: 61 modules topological, quiet when healthy.

## [3.10.0] — 2026-08-18 — FULL STACK BOOT (PHASE 3: RECOMMENDED TIER)

- **providers**: LLM abstraction on the Context — llm.generate,
  llm.failover, llm.usage; supersedes platform's migration-era llm
  service (later registration wins; one module object, one accumulator).
- **redteam / blueteam**: the mode modules register their console
  surface via hooks — menu entries (order 10/20) and launch verbs owned
  by module id.
- **knowledge**: kb.status/compile + kev.status services; journals
  whether the KB is built.
- **ops**: the operator verbs (export/debrief/replay/dossier/timeline/
  battle/approvals/panic/scope/clean/notify) + Operator Tools menu.
- **Tier-level disable=disappear PROVEN**: deleting redteam's manifest
  from the tree removes its menu entry, verb, and services while the
  other 11 modules boot untouched — the test that makes the architecture
  real, not cosmetic.
- Full boot: 12 modules topological (agent.graph, agent.memory,
  agent.nodes, platform, knowledge, ops, providers, tools, agent,
  blueteam, console, redteam), menu = [redteam, blueteam, ops],
  124 tools + 31 services, quiet when healthy.

## [3.9.0] — 2026-08-18 — CORE TIER COMPLETE (PHASE 2)

All four core modules ride the kernel; `controller.boot()` composes the
whole system from manifests alone:

- **tools** (`suijin/modules/tools/`): bridges every dispatch route —
  124 core + pack tools — onto the Context during boot. ctx.call_tool is
  now the kernel surface; route_tool remains the legacy surface,
  byte-identical behavior.
- **agent** (`suijin/modules/agent/`): the FIRST NESTED module —
  graph/nodes/memory sub-manifests resolve as first-class dotted-id DAG
  units (agent.graph boots before agent, which requires all three).
  Registry gained one-level recursion with dotted-id position
  validation (a nested manifest declaring a non-dotted id quarantines
  with a clear reason). Registers the agent-graph factory, nodes
  registry, and state schema as Context services.
- **console** (`suijin/modules/console/`): feature-blind by contract —
  the ConsoleHooks registry (register_menu/register_verb/
  unregister_owner) is the extension point every surface renders from.
  Disable-means-disappear proven by test: unregister_owner removes the
  module's entries from menu() and verbs.
- Full boot proof: 7 modules in topological order (agent.graph,
  agent.memory, agent.nodes, platform, tools, agent, console), 124
  tools + 12 services materialized on the Context, journal records the
  sequence, reverse-order shutdown.

## [3.8.0] — 2026-08-18 — FIRST MODULE STANDING (PHASE 2 BEGINS)

- **platform module** (`suijin/modules/platform/`): the first core-tier
  module boots through the kernel — plugin.json manifest (tier: core,
  entry string), registers config/workspace/llm/traffic_scorer services
  on the Context (the Phase 0 services seam graduates into module
  registration), initializes runtime + workspace directories on start,
  journals the boot.
- **controller entry materialization fixed**: imported module objects
  never landed in the boot entries (register/start silently skipped for
  manifest-sourced modules). Found by the new platform boot tests —
  exactly why tests come first.
- Context is now the source of truth for the workspace path: the module
  honors ctx.workspace (was returning the global — test caught it).
- Boot sequence proven end-to-end: scan suijin/modules -> resolve ->
  materialize from entry string -> register -> start -> services
  materialize -> shutdown. No injected objects anywhere.

## [3.7.0] — 2026-08-18 — RUST HEART (PHASE 1.5)

The compiled core exists and the kernel uses it:

- **native/suijin-core crate** (PyO3, abi3-py310, serde): `resolve_dag`
  and `check_paths` — exactly two functions cross the boundary, both
  JSON-in/JSON-out, zero Python object graph. 5 Rust unit tests; builds
  warning-free; deterministic BTreeMap serialization.
- **Pure-Python oracle** (`kernel/_pure.py`): permanent fallback AND test
  oracle. **Native shim** (`kernel/native.py`): wheel -> dev-build ->
  pure, the only file that may touch the compiled module.
- **Oracle suite** (`test_native_oracle.py`): 15 fixtures + 600-case
  fuzz (generated trees + path strings), canonical-JSON equality. On its
  first run it caught (1) a real divergence — unsorted core-abort text
  in Rust — and (2) a real algorithm bug shared by Rust, the oracle, AND
  the original registry: a module whose dependency was skipped still
  booted. Both fixed in all three implementations.
- **registry.resolve()** now delegates the graph math (cycles, fixpoint,
  core-abort, topo order) to the compiled core via the shim; collision
  policy and quarantine remain object-level. The dead Python DFS is
  deleted. Pure fallback keeps every environment working — `pipx install
  suijin` still never needs a Rust toolchain.
- maturin wheel build + CI matrix deferred to stable network (crate +
  dev-build path proven locally).

## [3.6.0] — 2026-08-18 — KERNEL COMPLETE (PHASE 1)

All 12 kernel subsystems live; controller boots with a full POST:

- **jobs**: kernel JobScheduler (spawn/get/status/output/list/cancel,
  200-job cap) — tools/job_registry delegates here in Phase 2.
- **vfs**: the single file-chokepoint — workspace-anchored resolution,
  symlink-escape detection, allowlist extras, boundary-checked writes.
- **security**: the permission vocabulary (network/shell/filesystem/
  provider/events.*) — declared in manifests, validated at parse,
  enforced at one point, renderable by the Module Manager.
- **config**: LayeredConfig — kernel -> module -> user -> env shadowing,
  immutable snapshots; import-order-dependent config state dies here.
- **health**: per-module last-boot status feeding boot report + doctor.
- **journal**: dmesg analog — ring buffer + rotated disk log; the boot,
  module lifecycle, and shutdown are recorded on every run. (Named
  journal, not logging — no stdlib shadowing.)
- **errors**: BootError/DependencyError/PermissionDenied/QuarantinedModule.
- **controller** now wires Vfs, JobScheduler, Journal, and HealthTracker
  into every Context; records module.start/skip events; flushes the
  journal on reverse-order shutdown.
- **POST test** (test_integration_boot): one boot exercising every
  subsystem — scan realistic tree (incl. a broken module), verify tools/
  services/events delivery/jobs/vfs boundaries/journal on disk/health
  counts/quiet-boot output/shutdown. 68 kernel tests total.

## [3.5.0] — 2026-08-18 — KERNEL DAWN (PHASE 1 CORE)

`suijin/kernel/` exists — stdlib-only, purity-linted, 45 kernel tests:

- **contracts**: Module/Tool protocols (structural), Tier IntEnum
  (CORE/RECOMMENDED/INSTALLED) — the kernel understands categories of
  software, never specific modules.
- **EventBus**: synchronous pub/sub with per-subscriber fault isolation
  (a broken listener can never break the chain) — the replacement for
  every lazy cross-import hook.
- **Context**: the syscall table handed to every module — config,
  workspace, events, lazy services, namespaced tool registry with
  contained failures.
- **Registry**: manifest parsing, recursive scan, dependency DAG with
  cycle NAMING, tier collision policy (later tier loses unless
  `overrides` declares the shadow), broken-manifest quarantine,
  fixpoint availability resolution, BootReport with human summary.
  Boot-simulator fixtures cover healthy/missing-dep/circular/collision/
  broken trees.
- **controller.boot()**: the composition root — scan -> resolve ->
  register (all) -> start (topological order). Core failures abort;
  recommended/installed failures skip + report. Quiet-boot contract:
  silent when healthy, report exactly when degraded. ctx.shutdown()
  stops in reverse order, best-effort.
- **Kernel purity linter** (test_kernel_purity): kernel files may import
  only stdlib + suijin.kernel.*, verified by AST AND clean-interpreter
  import — the architectural keystone, enforced from day one.
## [3.4.0] — 2026-08-18 — PHASE 0 COMPLETE

Suijin OS groundwork finished — the tree is de-coupled and kernel-ready.
Behavior identical throughout; 865 tests green.

- **One job registry** (`tools/job_registry.py`): two registries existed —
  runtime.py's was dead weight (exported, never populated) while the real
  one lived as privates in nodes/execute_tool_node.py, which tools/jobs.py
  reached into. The registry is now a proper module (spawn/get/status/
  wait/output/list/cancel, capped at 200 tracked jobs); the node spawns
  through it, the job tools read through it, and the sync->background
  auto-promotion path adopts its thread into it (a ruff-caught dangling
  uuid reference exposed that path was still hand-rolling entries).
- **All 8 tools->core inversions eliminated** via `tools/services.py` — a
  stdlib service seam (proto-Context): core registers lazy producers at
  runtime-init; battle/housekeeping (blue scorer), providers (config
  loader, active model), and run_commands (audit/report/sessions) now
  import only the seam. Enforced by `test_no_core_inversions.py`, which
  AST-fails on any future tools->core import beyond core.constants.
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
- **Import-time side effects -> `init_runtime()`**: module discovery, TLS
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
approve->deny->clear roundtrip · packaging guard suite.

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
  deploy->analyze->route sequence blueteamer drives.

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
  Packaging guards lock pyproject<->version.json (the 2.3.0-beta drift
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
  codebase scanners, session_control; packaging guards; Medusa->Suijin
  migration E2E incl. a full install.sh run against a fake $HOME with a
  legacy ~/.medusa — marked `slow`, CI runs it).
- Coverage 54% -> **60%**; gate raised 48 -> 56.

### Not built (asset/liability filter — see 2.11.0's list)
- Per-tool output parsers beyond nmap/dirs (LLM reads text fine; offload
  handles size), rollback manager, team sync, post-exploit cleanup
  orchestrator — each would add state machines with no current consumer.

## [2.12.0] — 2026-08-18 — SUIJIN (FULL PRODUCT RENAME)

### Changed — Medusa is now Suijin, everywhere
- **Package**: `medusa/` -> `suijin/` (git-tracked rename; history kept).
  Every `from medusa…`/`import medusa` across code, tests, CI, Docker, and
  docs now targets `suijin`.
- **CLI**: the `medusa` command is now `suijin` (`suijin doctor`,
  `suijin pull kb`, `suijin ui`, …). argparse prog, help text, and every
  doc example updated.
- **Workspace**: `medusa_agent/` -> `suijin_agent/` with automatic data
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
  (record_finding -> check_knowledge roundtrip, invalid finding types).
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
- **Coverage floor** 40 -> 48 (measured 52%, 4-point buffer).

### Added — WebUI
- **Dossier view**: target search -> constraint/failure/history/report
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
  reports per-source index-vs-cache staleness (newer tarball -> rebuild,
  unindexed cache -> pull). `suggest_exploit` fuzzy-matches GTFOBins bins
  (difflib, cutoff 0.75 — `finnd` -> `find`).
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
  flags, route hints, latency) -> capability-matrix baseline.

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
  blue_target lab fresh, runs a scripted red campaign (recon -> auth ->
  access -> injection chain -> sweep; 12 attack classes, flag capture) while
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
  battle traffic: 0.14 -> 0.57 at threshold 5 (0.86 at threshold 2),
  precision held ≥ 0.80.
- XXE bodies (`<!ENTITY`) and privilege-spoofing headers (`X-Admin: true`,
  `X-Role: admin`) were never inspected — both now signal at weight 5.
- Battle-time effect: blue score vs the same scripted campaign went
  35 -> 135 with an actual network block landing mid-campaign.

## [2.8.0] — 2026-08-17 — ABYSS CONSOLE (WEB DASHBOARD)

### Added
- **`suijin ui`** — local-first web dashboard for the operator:
  - **Frontend**: React 18 + TypeScript + Vite single-page app in `webui/`
    (sources) built to `suijin/ui/dist/` (committed — works without Node).
    "Abyss" design system: glass-morphism cards, neon-accent interactions,
    no shadows/eyebrow-lines, Gotham Medium (Montserrat stand-in — Gotham is
    commercial), Instrument Serif display stats, JetBrains Mono terminals.
    Dark theme only, responsive (12-col -> 2-col -> 1-col).
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
  `suggest_exploit` (fingerprint -> GTFOBins privesc page + HackTricks +
  PayloadsAllTheThings leads), `find_wordlist` (SecLists keyword search that
  **materializes** files into `suijin_agent/wordlists/` from the cached
  tarball — the DB copy can be truncated), `extract_payloads` (KB code
  blocks -> `suijin_agent/payloads/`, 8-16k size window), `kb_stats`
  (inventory), `wordlist_tool` (merge/dedupe/length-filter), `mine_failures`
  (SequenceMatcher clustering of `failure_db.json`), `anonymize_report`
  (regex scrubber for IPs/emails/bearer tokens/api keys/JWTs/private keys ->
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
- **README rewritten documentation-first** — 1,429 marketing-heavy lines ->
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
- version.json -> 2.4.0 (codename Back To Roots).

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
- Test suite: 345 -> 360 tests.
- version.json -> 2.3.0-beta (codename Shellforge).
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
- **Test suite: 286 -> 345 tests** (14 files)
- **Coverage: 35% -> 40%**; CI floor raised to 35%
- version.json -> 2.0.2 stable

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
- **Test suite: 83 -> 134 tests** (7 test files)
- README updated: accurate test counts, new file structure, pytest command, links to CONTRIBUTING.md and ADRs
- README table of contents: added Contributing + ADRs links

## [2.0.0] — 2026-08-12

### Added
- **Blue Team SOC** — autonomous defensive security agent
- **HTTP Forward Proxy** — transparent traffic interception for any app
- **18 Attack Pattern Detectors** — SQLi, XSS, SSRF, SSTI, XXE, CMDi, LFI, JWT, deserialization, LDAP, NoSQL, mass assignment, auth bypass, brute force, file inclusion, GraphQL, scanner UA
- **Per-Endpoint AI Subagents** — one per discovered endpoint, full codebase ingestion
- **Live Tarpit** — real request delays (0.018s -> 5.8s) via shared state file
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
