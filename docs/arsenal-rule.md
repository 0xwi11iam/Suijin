# The Arsenal Rule — a standing contract (2026-11-02)

## The rule (operator ruling, permanent)

**If a tool exists, the agent uses it.** "The AI didn't know it could do
that" is a harness defect, never a model defect. Every tool in the
manifest must be (a) visible, (b) taught, (c) reminded, (d) verified —
and the harness proves all four, it never assumes them.

## The four guarantees

### 1. visible — the tool exists in the agent's world

Every registered tool renders in the live tool catalog with args format
and when-to-use. The catalog is per-phase (all tools, all phases). The
`tool_inventory` tool shows the full kit on demand — catalog tools PLUS
installed binaries (capability-checked, never guessed).

**Contract test:** `test_every_registered_tool_in_catalog` — the
dispatch routes and the catalog cross-check; a tool routed but invisible
(that was nmap/gobuster/katana for months) fails CI.

### 2. taught — the tool explains its own job

Catalog purpose lines are one sentence of WHAT JOB the tool does, not
what it's named. Tool contracts (usage docs) ride below the catalog for
the top-15 tools. Doctrine in RULES names the pairings: injection →
inject_probe; comparisons → http_replay(compare); fuzzing → ffuf_fuzz;
403s → bypass_403; chains → chain_suggest.

**Contract test:** the catalog prompt contains every tool's purpose AND
the pairing rules; budget-adjusted, never silently cut.

### 3. reminded — the harness coaches at decision time

Three passive layers, zero model cooperation required:
- **tool_doctrine**: args that smell like a better tool's job carry the
  upgrade notice ON THE RESULT (FUZZ→ffuf_fuzz; payloads→inject_probe;
  hand-rolled compares→http_replay)
- **tunnel-vision**: 4+ identical calls with no finding → the result
  carries the untouched coverage gaps + alternatives
- **indicator firing**: chain templates surface at observation time

One active layer:
- **the arsenal reminder** (NEW): at iterations {10, 25, 50} the
  supervisor lists HIGH-VALUE tools never used this engagement, each
  with its one-line job, and names the work the agent already did that
  the tool would have done better. Cooldown-gated; fires at most 3× per
  engagement; never during an active chain step.

**Contract test:** `_detect_unused_arsenal` returns the reminder when
inject_probe/http_replay/etc. are absent from the trace past the
iteration threshold; returns None for a trace that used them.

### 4. verified — usage is measured, not hoped for

The engagement trail records every tool call (already: agent_steps.jsonl).
NEW: the end-of-run audit (end_audit) reports **tool coverage**: which
high-value tools ran, which never did, with the count. The operator
sees "arsenal usage: 14/22 tools — never used: race_test, dispatch_testers,
chain_suggest" in the final summary. Chronic gaps are prompt/catalog
bugs to fix, not model failures to shrug at.

**Contract test:** end_audit's output includes the arsenal-usage block.

## The enforcement ladder (what happens when the rule breaks)

1. A tool is routed but not in the catalog → CI fails (visibility test)
2. A tool is in the catalog but the agent doesn't use it for work it
   exists for → the result-level notices fire; the arsenal reminder
   names it by iteration 10
3. Still unused at end of run → the audit names it; the operator decides
   whether the tool, the teaching, or the doctrine is the problem

## What this rule does NOT do

- Does not force-call tools (the model still chooses; the harness makes
  ignorance impossible, not choice illegal)
- Does not spam: every reminder is cooldown-gated and content-bearing
- Does not grow the prompt unboundedly: the catalog budget stands; new
  tools must earn their line by being used

## Implementation status

| Layer | Status |
|---|---|
| visibility cross-check test | EXISTS (test_tool_inventory) |
| tool_doctrine notices | EXISTS (tool_doctrine.py) |
| tunnel-vision signal | EXISTS (supervisor.py) |
| indicator firing | EXISTS (chain_engine) |
| arsenal reminder (supervisor) | NEW — this session |
| end-audit tool coverage | NEW — this session |
| dead-key log (google_key_probe) | NEW — this session |
