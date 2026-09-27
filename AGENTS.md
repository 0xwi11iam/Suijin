# AGENTS.md — standing rules for any agent working in this repo

## Agent memory vault (local-only, gitignored)

`agent-vault/` (gitignored, never committed) is the durable decision/session
memory — read it FIRST on a fresh session, and record substantive decisions
there as they're made. Its engagement notes are exempt from the rule below
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
