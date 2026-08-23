# Suijin — Blueteamer Roadmap (SOC-in-a-box)

> Stashed so it survives context loss. Status: Wave A committed (`024b696`).
> Waves 1-8 pending. Red-teamer TUI wave tracked separately (see bottom).

## The Loop (memorized — every future blue feature enters through it)

```
process_event(event) -> Incident | None          # the ONE decision path
├─ ingest    normalize -> event schema (ts, src, method, path, query, body, ip, ua, headers, status, user_hint)
├─ enrich    identity resolve (JWT/Bearer/cookie->user) · asset match · first-seen flags
│            · KG attacker history · authorized-engagement check (red state) · IOC match
├─ suppress  known-normal (normalizer) · FP allowlist · dedup (same actor+signal -> attach to existing Incident)
├─ score     detector + custom rules + config weights (wave-A scorer)
├─ verdict   benign | suppressed | suspicious | critical  (tier1/2 logic folded in; opens/attaches Incident)
├─ respond   action registry: tarpit · block · deceive ladder · contain (disable_user /
│            revoke_key / kill_session) · playbook chains · dry-run + audit · AUTO re-verify (lab)
├─ record    Incident timeline · IOC extraction · KG update · retention shard · stage metrics
└─ notify    webhook/macos/file — on state transitions, throttled
```

Fast path: score >= critical AND known class -> playbook NOW (no LLM).
Slow path: ambiguous -> LLM analyst sees the CASE (actor history, open incident,
prior actions + outcomes) -> verdict, confidence, IOCs, proportionate action.

Incident lifecycle: new -> contained -> monitoring -> closed; REOPENS + escalates
on new activity by same actor. Event-driven, no timers.

## Anti-dead-code laws

1. Features ship only as: loop stage | ingest adapter | response action | state-reader command. No third kind.
2. A stage's output must have a named consumer. Execute-and-discard is deleted at design time.
3. Per-stage metrics always emitted (suppressed, escalated, contained, mttd/mttr). Zero-counter stages get removed.
4. Headless-first: `suijin blue --lab | --replay F [--dry-run]` runs the identical loop in CI. TUI is a renderer, never an owner.
5. Asset checklist (merge gate): scenario exercises it · counter proves it · budget bounds it · failure drill protects it · operator sees it · it can be undone · deletion would be noticed.

## The 8 scenarios (hard CI acceptance bar, live lab or fixtures)

1. 50 SQLi probes, one IP, 10s -> ONE incident, 50 events attached, 1 tarpit        (dedup)
2. Health-checker with `select` in a JSON field -> benign -> allowlisted -> silent   (FP loop, LLM judgment)
3. Login bob, then SQLi with bob's token -> identity resolved -> bob disabled
   -> bob's token rejected by the lab                                              (enrich->contain chain)
4. LLM provider down -> fast path + ladder respond anyway, incident marked degraded (no AI dependency)
5. Slow scan + SQLi + token tamper, 30min apart, same actor -> one campaign
   incident, severity escalates                                                     (correlation, reopen)
6. Session 2 sweeps retained traffic, finds session 1's IOC -> hunt incident        (memory compounds)
7. Battle: red agent fires -> enrich tags authorized: engagement-42 -> no
   self-containment                                                                (friendly fire)
8. Every incident carries mttd/mttr; bench blue column + regression arrows          (measurement)

## Waves

- **Wave A — DONE (024b696)**: BASE_DIR lab path, tarpit file protocol unified
  (`set_at`->`since` via defense/tarpit.py engage/delay_for), 9 attack classes ported
  to core detector + blind SQLi, custom rules wired into scorer + TUI fast path,
  config-driven scorer weights/thresholds, load_blue_config deepcopy bug.
- **Wave 1 — Loop alive** (scenarios 1, 2, 7): blue/loop/ package — event schema +
  adapters (lab/proxy JSONL, nginx, generic JSON), enrich (identity, first-seen,
  authorized-engagement), suppress (allowlist + dedup), score, Incident store +
  lifecycle, record + stage metrics. REPLACE LiveFeed.process_request path (TUI =
  renderer), DELETE soc/* classes + watchers spawner, headless `suijin blue
  --replay/--lab/--dry-run`.
- **Wave 2 — Loop acts + safe** (scenarios 3, 4, 5): fast/slow paths; action registry
  keyed by actor tier; containment (disable user, revoke api_key/reset_token, kill
  session); AUTO re-verify in lab (re-fire payload, record result); deception ladder
  as AI-down fallback; canary deploy->watch; webhook notify channel; incident reopen
  state machine. AG1: timestamped backup before ANY deploy mutation ->
  .suijin_backups/, `suijin blue revert`, dry-run default for external codebases.
- **Wave 3 — Loop remembers** (scenario 6): retention shards (workspace blue_traffic/,
  rotate, cap — truncation dies), IOC extraction + retro-sweep (`suijin blue hunt`),
  cross-session first-seen intel, `/fp` command.
- **Wave 4 — Loop proves** (scenario 8): battle/bench/spar drive the loop; blue column
  (detection rate, precision, MTTD/MTTR/dwell) + regression arrows; session markdown
  report; ATT&CK tags + coverage heatmap.
- **Wave 5 — Trust**: AG2 rent ledger + `blue health` + decay; AG3 cost-per-verdict +
  daily_budget_usd enforcement; AG5 scrub-at-rest hygiene (reuse anonymize_report);
  AG6 durability drills (corruption, provider-death, 1000-event burst).
- **Wave 6 — Product**: `blue init` (onboard + log-seeded baseline via
  parse_nginx_log -> normalizer.train), `blue drill` (purple exercise, both scored),
  fleet/multi-target + `blue status`, gateway blue endpoints (incidents/metrics/
  coverage + WS stream) as state-readers.
- **Wave 7 — Compounding**: cross-session intel merge, LLM-drafted rules from misses
  (dry-run -> operator promote), playbook evolution from verification outcomes,
  per-entity UEBA signals.
- **Wave 8 — Scale & close**: perf budgets under volume, TLS-MITM proxy as ingest
  adapter, release notes + housekeeping.

## Architecture laws (from the gap analysis)

- One loop, one state, two doors. The box compounds: every threat feeds a case that
  enrichment, memory, and identity-aware containment act on; the next threat
  inherits everything learned.
- Suijin blue was a WAF with an LLM; a real SOC's loop is enrich -> detect -> case ->
  contain at identity level -> measure -> hunt retroactively.
- Never again: parallel modules waiting for a caller (ops.py, deception_engine,
  canary_token, auth_mapper, watchers, soc tiers — the first graveyard).

---

# Red-teamer Console UI wave (current)

Scope (locked): scrolling transcript + pinned Live strip (battle.py in-line Live
pattern), Rich only, no emojis. Per-iteration block: iteration header; `thinking`
thought line (cyan); `:: why ::` reasoning line (dim italic, `/think` toggle);
`> tool` + Syntax-highlighted command block (bash/json/python/markdown per tool);
boxed dim output (2000 chars); colored loot lines (FLAG{...} -> #e6b47c, creds ->
bold green); supervisor/oracle/drift/fireteam renders; live strip = iteration ·
phase · tokens · cost (accurate per-model) · FLAG n · CRED n · fireteam n.

Bug fixes in the same wave: stale `_current_step` re-execution; trace
success/error_class back-fill (truthful `!`); audit observation from execute
event; plan_tools remaining steps; ask_operator double print; pricing (unbound
`text` in estimate path, glm-4.6/Qwen3-Coder/zai-org ids, case-insensitive match,
`priced` flag semantics, estimate fallbacks for gemini/HF/anthropic, lobstertrap
usage); fireteam timeout 15s -> 60s + retry; install.sh dev-mode auto-detect
(script dir checkout -> dev default) + `--dev [path]` flag.

UI module: suijin/modules/redteam/lib/red/console_ui.py (EngagementUI). Wire-in:
redteamer.py astream loop calls ui.* — no logic changes. Prompt gains `reasoning`
field request (1-2 sentences). Loot -> audit findings live.
