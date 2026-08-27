# Live Streaming & Transport — operator notes

What changed, why it's faster, and how to see the proof.

## The stream

zai and deepseek completions now **stream** (`stream=True` SSE). Tokens
arrive as they are generated instead of after the entire response:

- BOTH **reasoning** and **content** deltas stream into the *flexing
  box* — a bordered panel inside the bottom bar that grows line by line
  while the model generates (glm often emits content with no separate
  reasoning stream; a sink that ignored it made streaming invisible).
  The live box shows WHILE STREAMING always; when the iteration block
  prints it collapses. `/think` governs the transcript's permanent
  said-section, not the live view.
- Streaming changes ZERO agent behavior: the decision still parses from
  the fully-assembled text, exactly as before.
- Fireteam subagents share the same stream; their deltas interleave in
  the box (cosmetic, tail-capped).

## The proof

`/cost` now shows `first token X.XXs` — measured from think-turn start
to the first streamed reasoning token. If streaming works, that number
is ~1s; the old behavior (wait for the full response) measured as
10-20s.

## Transport: TLS smoothing + honest timeouts

- One shared `requests.Session` for all provider calls: the TLS/TCP
  handshake happens once, keep-alive carries it between turns.
- Timeouts are `(10 connect, 300 read)`. Long completions are
  legitimate; the old flat 45s read timeout killed exactly the biggest
  responses and retried the whole call.
- The UI-side 90s hard cap is now 600s (progress is visible, so long
  generations are fine; only a stuck transport gets cut).
- Transport failures **diagnose** instead of dumping tracebacks:
  `TLS handshake failed — VPN/proxy/cert issue`, `DNS failure`,
  `connect timeout`, `read timeout — provider stalled`. A diagnosed
  `Error:` string always comes back — the agent loop never sees an
  exception.
- Mid-stream transport death falls back to ONE non-streaming attempt
  before the normal backoff.
- Usage accounting is unchanged: tokens come from the stream's
  `include_usage` final chunk, estimate fallback preserved.

## Lobstertrap

The provider-path lobstertrap hook is **removed**: the per-call
`localhost:8080` probe added latency to every request and silently
rerouted traffic when anything happened to listen there.

## /exploit — instant exploitation

```
suijin exploit <target>          # or Operator Tools → EXPLOIT now
```

No menus, no recon phases. The objective orders the agent to mine
everything already known first (`check_knowledge`, `target_dossier` —
verified vectors, blocked patterns, creds, failed attempts), then fire
the highest-probability payload immediately. Every attempt is noted
(`write_note`), every confirmation recorded (`record_finding`), proof
demanded. The authorization ledger gate is unchanged — a target
without an authorization record gets a visible warning.

## The input box (red console)

The operator prompt is a white box pinned as the strip's bottom row —
always visible no matter what is running:

```
[⠋] thinking  RECON  » type here▌
```

- **thinking + spinner** on the left (live, animated)
- **mode badge** next to it: RECON / EXPLOIT / REPORT — **Tab cycles**;
  the mode tags the prompt you give (plain lines dispatch as
  `[RECON] focus on /admin`-style guidance; slash commands work in any
  mode)
- **ESC ESC** (two escapes within 0.6s) **pauses the agent** — the
  Ctrl+C replacement; arrow keys are swallowed and never reach the
  buffer; Ctrl+C still works as a backup

## Engagement bundles (.sje)

Every concluded engagement saves to `suijin_agent/outputs/exports/<name>.sje`
— a hash-sealed zip carrying the graph state (messages, chain memory,
phase, todos), the exploit catalogs (POCs + receipts), and the config
(secrets stripped). Resume anytime:

    suijin load <file.sje>        # or Operator Tools → Resume a saved engagement

The saved state is injected into a fresh graph thread — the agent
CONTINUES with full memory (what it tried, what failed, what was
proven) instead of starting over. Tampered or corrupt bundles are
refused with the exact reason.

## Engagement bundles (.sje)

Every concluded engagement saves to suijin_agent/outputs/exports/NAME.sje
— a hash-sealed zip carrying the graph state (messages, chain memory,
phase, todos), the exploit catalogs (POCs + receipts), and the config
(secrets stripped). Resume anytime:

    suijin load FILE.sje      # or Operator Tools -> Resume a saved engagement

The saved state is injected into a fresh graph thread — the agent
CONTINUES with full memory (what it tried, what failed, what was
proven) instead of starting over. Tampered or corrupt bundles are
refused with the exact reason.
