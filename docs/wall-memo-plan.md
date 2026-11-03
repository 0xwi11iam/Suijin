# The Wall Memo — closing the gap (2026-11-03)

## The diagnosis (operator's words, kept verbatim as the spec)

The agent has "a pile of tools, jails, and refusals, and it learns the
machine by crashing into it." The successful path is not the path the
tools actually allow. Scored: single clever action 90 vs 70 (suijin
ahead); still working at hour three 40 vs 90; replayable finding 35 vs
95. The blend hurts where it matters: **the agent cannot keep going.**

Five slices. Slice 1 alone leaves the score near 60 — it stops the
machine lying; it does not close the gap.

## Slice 1 — the command works the first time

**Goal:** the model's first call succeeds; denials teach; large output
is retrievable; long tools background themselves.

1a. **Work directory pre-exists + is THE cwd.** Engagement home already
    does this; the remaining gap is tools that hardcode `/tmp` — audit
    the module tree for `/tmp/` literals and route them through
    workspace resolution (mitmproxy capture file is the named offender).

1b. **Proxy + CA injected in executor_env.** The same seam credentials
    use: `HTTP_PROXY/HTTPS_PROXY/SSL_CERT_FILE/REQUESTS_CA_BUNDLE/
    CURL_CA_BUNDLE/NODE_EXTRA_CA_CERTS/GIT_SSL_CAINFO` pointing at the
    recording proxy (slice 3). When the proxy is off, unset — never a
    dead proxy env. curl/requests/node/git trust the intercept without
    -x or -k.

1c. **Denials name the platform, the rule, and the exact next call.**
    Guardrail messages gain a "do this instead" line: the denied command
    + the tool call that would have worked. Missing-binary errors append
    the alternate command (`testssl.sh` missing → the openssl one-liner;
    hashcat missing → john format hint).

1d. **Contract ↔ implementation drift test.** One CI test feeds every
    schema'd tool's advertised actions/params into the real dispatch and
    asserts no `unexpected keyword` / `Unknown argument` — the
    coverage_check/compare=true crash class dies at commit time, not at
    iteration 12.

1e. **Large output lands in a file the model reads.** execute_terminal
    above N chars writes the full output to `outputs/<ts>.txt` and the
    tool result names the file + shows head/tail. The model can read
    what it just did.

1f. **Long tools auto-background.** A tool that exceeds ~10s foreground
    promotes itself to a job (the auto-bg machinery exists); the result
    says "backgrounded as job-N — job_output when ready." The model
    should not have to remember `background: true`.

## Slice 2 — rows the next turn reads

**Goal:** state that survives compaction; a snapshot the think context
opens with; exhausted ≠ done.

2a. **rows.py (BUILT this session)** — facts / assets / intents /
    findings as JSONL in `<engagement>/state/rows/`. Code-computed ids,
    idempotent inserts, parent links, the snapshot. Files, not Postgres.

2b. **Wire the rows into think context** — CURRENT_STATE opens with
    `snapshot()`: counts, open intents, last facts, untested assets.
    record_fact becomes the close-out of an intent (one fact per
    intent, not one per curl).

2c. **Exhausted-not-done** — on budget/gate-refusal/subagent-cap: one
    turn with execute_terminal REMOVED, write the rows, mark exhausted.
    The completion gate treats exhausted intents as open work.

2d. **Dispatch tools** — `add_intent`, `set_intent_state`,
    `list_intents` registered in dispatch + catalog, so the MODEL can
    write rows too (not just the harness).

## Slice 3 — index the proxy

**Goal:** every request searchable; findings bound to exchanges;
observation detectors run on recorded traffic, not only on
polite tools.

3a. **mitmproxy capture → SQLite index.** Flows land in
    `<engagement>/traffic/index.db`: method, url, host, status, size,
    body FTS, blob spill for large bodies (content-addressed).
3b. **traffic_search(host=required, body text, thin rows)**,
    **traffic_get(id)**, **traffic_blob(hash)** as tools.
3c. **Fix mitm_get_requests/mitm_analyze_flow** — the named broken
    pair; they answer from the index.
3d. **Observation engine on the index** — IDOR/BFLA detection over
    recorded exchanges; findings auto-attach exchange ids ("none" is
    honest for non-HTTP).
3e. **Worker prompt line:** "search traffic before you curl the same
    URL again."

## Slice 4 — split the brain, keep the TUI

**Goal:** a planner that cannot run commands; a worker that claims one
intent and stops. The TUI unchanged.

4a. **Planner call** (separate LLM invocation): tools are add_intent,
    prove_goal, steer, kill ONLY. Fires when a worker finishes, a
    finding lands, or the queue is empty-and-goal-open (must emit ≥1
    intent — the empty-queue rule: an instruction, not a wall).
4b. **Main loop claims one intent** — the intent text in the system
    prompt (compaction cannot drop the job); coverage stripped from the
    worker snapshot so it does not wander; on budget, Bash hidden, one
    wrap-up turn writes rows, marks exhausted.
4c. **Todo file holds serial chains** — step 3 dispatches only when
    step 1's parent fact exists.
4d. **prove_goal** checks measured numbers (class weights AND asset
    percent); the last open goal closing IS the completion path;
    free-text complete while a goal is open = a bug.
4e. **Constraints extracted once, concrete hosts, injected into every
    call** — prose in the objective is gone after compaction; a
    constraint row is not.

## Slice 5 — retest and disposition

5a. Statuses: pending/confirmed/fixed/false_positive/ignored/duplicate
    (two ids — row id and catalog EXP id — tools say which is which).
5b. **retest_finding** — a scoped run reading the stored proof +
    exchange ids; verdicts reproduced/fixed/inconclusive. One failed
    request is NOT fixed.
5c. **Report written from the rows**, not the model's last message.

## What we do NOT copy (operator's explicit calls)

- No planner-curl permission ("just three times") — that's a leak they
  wrote rules to plug
- No shared cross-engagement log — suijin already burned
- Class weights STAY; no asset-touch-percent replacement
- Secrets stay out of model text ($SUIJIN_CRED_*; ARTEX stores auth
  headers in traffic — we keep the redaction)

## What stays (suijin's lead — do not throw out)

http_replay/inject_probe/observation engine/credential redaction/
authorization gate/box+manifest/the lab/class weights/diffs.

## Order and dependency

1 → 2 → 3 → 4 → 5. Rows (2) before split (4) or the planner writes
plans nothing reads (the throwaway-dict bug). Proxy index (3) before
detectors move onto it. Slice 1 is independently landable and stops the
lying today.

## Status

| Slice | Status |
|---|---|
| 1a-1f | todo (1b needs 3's proxy up; 1a/1c/1d/1e/1f independent) |
| 2a rows.py | **BUILT** (unwired) |
| 2b-2d | todo |
| 3 | todo |
| 4 | todo |
| 5 | todo |
