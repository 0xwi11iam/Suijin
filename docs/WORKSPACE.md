# Workspace layout

Suijin state lives in exactly two places:

1. **The product repository** (`~/suijin`) — code, tests, docs, shipped assets.
   No engagement data, target-derived source, screenshots, or generated
   artifacts are committed.
2. **The runtime root** (`~/.suijin`) — all operator/runtime data.

## Runtime root

```
~/.suijin/
  config/            config.json, .env, providers.json, credentials, authorizations,
                     prompts/, skills/
  browser/           auth-profile/ (persistent login), sessions/ (cookie jars)
  intel/             knowledge_graph.json
  engagements/       one folder per chat/engagement
    index.json       generated map of engagements -> target/audit/findings
  artifacts/         global fallback only (pre-engagement tools)
  logs/              runtime logs
  swarm/swarm.db     agents, intents, findings
  mesh/              live agent bus state
  cache/  tmp/
  backups/           config and migration backups
```

## Engagement folder

```
~/.suijin/engagements/<slug>/
  engagement.json    name, target/program, status, timestamps
  events.jsonl       audit trail (the path shown in Finding History)
  home/              disposable agent sandbox
  exploits/          EXP-NNN/{description.md,exploit.yaml,run-N.log,catalog.json}
  inbox/             operator-provided text files (scope, notes, templates)
  .notes/  reports/  state/  toollogs/
```

Generated data for a live engagement belongs **inside its folder**. The
top-level `artifacts/` directory is only the fallback for tools that run before
an engagement is set. Findings store `engagement` (chat id) and the UI derives
the audit path from `~/.suijin/engagements/<engagement>/events.jsonl`.

## Rules

- Never write engagement data into the package tree or the repo.
- One engagement folder per chat; no loose files in `engagements/`.
- Every engagement must have `engagement.json` before agents start.
- Rebuild `engagements/index.json` with `python scripts/rebuild_engagement_index.py`.
- Delete an engagement only after its findings/evidence are archived or exported.
- Target-derived source/data never gets committed; `.gitignore` includes
  `wf/`, `$TMPDIR/`, `references/`.
