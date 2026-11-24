# AGENTS.md — standing rules for any agent working in this repo

## Agent memory vault (local-only, gitignored)

`agent-vault/` (gitignored, never committed) is the durable decision/session
memory — read it FIRST on a fresh session, and record substantive decisions
there as they're made. Read order: `operator-directives.md` (every standing
operator rule, cached the moment it was said), `task-stack.md` (open work,
top-down), `bugs-ledger.md`, `sessions.md`. Context compaction is NOT an
excuse to drop a rule or a task — they live here, not in the context. Its engagement notes are exempt from the rule below
*only because* the folder never reaches git; the exemption dies with the
gitignore entry.

## Docs and vault stay current (operator ruling, 2026-09-27)

EVERY substantive round updates the committed docs (`context.md`) and the
vault (`agent-vault/`) before the push: decision note when a decision
outlived the session, Bugs Ledger entry for every fix, session log line,
and `context.md` when repo facts changed (test counts, new modules,
architecture shifts). Stale docs are how the next session re-derives
wrong conclusions.

## Engagement confidentiality (PERMANENT operator ruling, 2026-09-26)

NEVER record an engagement's target, program name, vendor, or tested hosts
in ANY committed artifact — commit messages, code comments, test payloads
or docstrings, docs, CONTEXT.md, branch names. This is a privacy
obligation to the operator, not a style preference.

- Refer to engagements generically: "a live engagement", "the operator's
  current target", "a recent field run".
- Tests use `example.com` / `test.example` / documentation IP ranges —
  never a real target, and never a CDN+domain pairing that identifies the
  program by implication.
- Bug bounty programs carry confidentiality terms; naming the vendor in a
  public commit is a disclosure. When in doubt, leave the name out.
- Local-only files (workspace, prompt.md, engagement folders) are the
  operator's own machine and exempt; everything that can reach git is not.

Applied retroactively when found: if a target name is already in a
committed artifact, flag it to the operator and scrub on the next commit
(history rewrites only on explicit request).

## Nothing hardcoded (PERMANENT operator ruling, 2026-10-23)

No hardcoded values in committed artifacts, ever:

- No credentials or credential-shaped strings — test fixtures are
  self-evidently synthetic (`ghp_` + filler chars), never real keys.
- No engagement-derived data: target names, program intel, or learned
  knowledge in the tree. The knowledge graph's write path once pointed
  into the package dir and committed a live engagement's API keys and
  target intel — it is WORKSPACE-first now (workspace intel/
  knowledge_graph.json), with a neutral read-only seed in-tree.
- No machine-specific paths (`/Users/<name>`, `/home/<name>`) — derive
  from `$HOME`, the workspace, or config.
- Operator-tunable data lives in config/registry tables, not logic.
  Safety floors (the hard-guardrail denylist) are deliberately
  hardcoded — do not move them to config.

`suijin/tests/ops/test_no_hardcode.py` is the tripwire (secrets,
machine paths, package-tree writes). Engagement tokens for its scan
come from local-only `agent-vault/targets.txt` — never committed.

## One environment's fix must not break another (PERMANENT operator ruling, 2026-10-26)

Suijin runs on macOS, Linux, inside containers, over OrbStack and
Docker, on 8-color terminals and headless shells. Every fix must work
EVERYWHERE or degrade gracefully at its own boundary:

- environment-specific behavior lives behind a capability CHECK (color
  depth, TERM, platform, engine presence) — never a platform guess
- a guarded failure degrades to the general path (the box tui's verb
  fallback, the gray pair's A_DIM fallback, adopt's tar-stream when no
  mount exists) — it never takes the common path down with it
- subprocesses never inherit stdin (a supervisor's open pipe blocks
  container CLIs forever); tests run under pytest too — a feature that
  shells out must be test-safe or explicitly gated

## ACTIVE ENGAGEMENT PROTOCOL (overnight/extended bugbounty hunts)

While an operator-approved bug-bounty hunt is running, this protocol overrides
default assistant behavior:

- EVERY iteration = one executed action: a probe fired, an exploit attempted, a
  finding live-verified, or a submission draft written. A status-only response
  is a FAILED iteration.
- Watchdogs, monitors, sleep loops, and "check later" processes are FORBIDDEN.
  You are the monitor. You work while agents work.
- Post-compaction: your first action is reading
  ~/.opencode/plan/overnight-hunt.md (the queue) and
  ~/.suijin/config/config.json (scope), then executing the next item. The
  `hunt` agent mode + /hunt command embed these rules — prefer them for hunts.
- Confirmed findings: paste-ready draft on ~/Desktop same session, recorded in
  the findings store (swarm.db findings_v2) with AI-defined severity. Severity
  is never overridden by any system calculator.
- Blocked lane → switch to the next queue item immediately. Idling is the only
  unacceptable outcome.


## Runtime data lives only in ~/.suijin (operator ruling, 2026-11-24)

- The product repository (`~/suijin`) contains code, tests, docs, and shipped
  assets only. Engagement data never enters it: no target source clones,
  screenshots, evidence, artifacts, or target names in comments/tests/docs.
- All runtime state lives under `~/.suijin`:
  `config/`, `browser/`, `intel/`, `engagements/<slug>/`, `artifacts/`,
  `logs/`, `swarm/`, `cache/`, `tmp/`, `backups/`.
- One engagement folder per chat: `engagement.json`, `events.jsonl`, `home/`,
  plus generated `exploits/`, `.notes/`, `reports/`, `state/`, `toollogs/`.
  Global `artifacts/` is only the pre-engagement fallback.
- Every engagement gets `engagement.json` before agents start; findings point
  to their chat and `events.jsonl` audit path. Rebuild
  `engagements/index.json` with `python scripts/rebuild_engagement_index.py`.
- No Suijin state outside `~/suijin` and `~/.suijin`: no `~/0`, `~/.0`,
  `~/suijin_agent`, Desktop screenshots, stale launch agents, or temp copies.
