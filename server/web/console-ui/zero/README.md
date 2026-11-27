# `zero/` — the new-engagement landing

The console's landing (the "new chat" screen) is **0's native new-thread screen**, carried here
from upstream and adapted for Suijin. This directory holds that port. Nothing in it is loaded by
the console's own bundle except the small message protocol both sides share.

```
zero/
  home.html          the landing's document (a second Vite entry → dist/zero/home.html)
  entry/
    home.tsx         bootstrap: 0's stylesheet + tooltip provider + error boundary
    suijin-home.tsx  the landing itself — 0's markup and components, Suijin's content and wiring
    home.css         Suijin's palette, as token overrides on 0's dark palette
    protocol.ts      the only file shared with the console (messages + the submission validator)
  src/               0's source, mirrored (see "The mirror")
  MANIFEST.json      every mirrored file
  LICENSE-APACHE, LICENSE-MIT, NOTICE.upstream   upstream's licences, carried unmodified
```

## Why a second document

0's landing is not one component; it is a **233-file closure** of their source — the backdrop
system, their composer, their model catalog, their UI primitives, their stylesheet — and 65 of
those files are ones this console has modified for Suijin (`api.ts`, `transcript.ts`,
`turn-timeline.tsx`, the ui primitives…). Copying their files over ours would overwrite 65 files of
Suijin logic; copying only the missing files would leave their components running against our older
signatures.

So the landing runs in its **own document** (`home.html`), loaded same-origin in a frame by the
console ([`src/console/hero-frame.tsx`](../src/console/hero-frame.tsx)). That gives two hard
guarantees:

- **0's stylesheet can never reach the console.** Their tokens and utilities live in their document.
- **Our logic is untouched.** Session creation, the inbox upload, navigation, pause/resume/delete
  all stay in the console; the frame only collects the prompt and hands it over.

The two documents talk only through `entry/protocol.ts`. Both check `event.origin` and
`event.source`, and the console shape-checks every submission before it reaches the
create-at-submit path.

## What is 0's and what is Suijin's

Everything structural in `entry/suijin-home.tsx` is 0's, kept as written: the layout wrappers, the
context row, the composer card with its gradient hairline, the textarea + overlay, the controls row,
the suggestion row, and the class names on all of them. Every control is one of 0's prebuilt
components (`Backdrop`, `Button`, `Tooltip`, `DropdownMenu`, `ModelCatalog`, `ModelIcon`,
`LoadingDots`).

Suijin's parts are marked `suijin:` in the source:

| 0 | Suijin |
|---|---|
| project selector, in the context row | **Mode** — BBP · VDP · CTF · Pentest |
| the "YOLO" autonomy control in the composer | **Permissions** — Recon · Exploitation · Maximum |
| model catalog from 0's engine | Suijin's `/api/console/runtime` + `/api/console/available-models` |
| submit starts a 0 mission | submit hands the prompt to the console's create-at-submit path |
| `Ask anything, / for commands, @ for context` | `What should we hack today?, or drop a scope file` |
| 0's suggestions, onboarding, `@` integrations | three Suijin suggestions; the rest is not shown |
| 712px column, 64–224px composer | up to 1120px wide, ≥128px tall |
| neutral charcoal | Suijin's palette (`entry/home.css`), measured from 0's own screen |

Pentest mode blocks submit until a scope document is attached, exactly as the console enforces it.

## The console's side

`src/pages/console-page.tsx` renders `<HeroFrame>` where the old inline landing was, and nothing
else about it changed. `startFromHero` does what `startFromLanding` did — set the root draft, call
`submit(true)` — after writing the frame's selection into a ref the create branch reads, so a
session is never created with the previous selection.

The frame waits to hear the outcome: the console accepts **once a session exists and its inbox is
in place** (and clears the stored draft itself, because it navigates away immediately), or rejects
and the frame keeps the operator's text and files and shows why.

## The mirror

`src/` is [0sec-labs/0](https://github.com/0sec-labs/0), `packages/web/src`, at commit
`78d453c05afef2819fbc1710acfb6d2e6da633c3` (MIT OR Apache-2.0, © 2026 0 Labs — see the licence
files here). It is **verbatim** apart from four mechanical rewrites, applied so the files resolve
inside this tree:

1. `"@/…"` → `"@zero/…"` in 71 files, so upstream modules import *each other* and never this
   console's modified copies (`@zero` is a Vite alias → `zero/src`).
2. `fonts.css`: `url(../node_modules/…)` → `url(../../node_modules/…)` — the mirror sits one
   directory deeper than upstream's package.
3. `components/brand-mark.tsx`: the two wordmark svgs, which upstream keeps at its repository root,
   are repointed to copies under `src/assets/brands/`.
4. `types.ts`: two engine modules our vendored `@0/engine` does not have (`executed-poc`,
   `reproduction`) are repointed to copies under `src/engine/`.

Everything else is byte-identical to upstream. `MANIFEST.json` lists all 233 files.

**Do not hand-edit `src/`.** It is upstream's; a local fix there is lost on the next refresh and
hides the real divergence. Adapt in `entry/`, or change the console. To take a newer upstream:
recompute the import closure of `console/conversation.tsx`, `ui/backdrops`, `lib/web-appearance`
and the three root stylesheets, copy it in, re-apply the four rewrites, update `MANIFEST.json` and
the commit hash above.

`src/` is deliberately outside the console's `tsconfig` `include` (it typechecks against upstream's
newer engine); it is compiled by Vite, which reports any import that does not resolve.

## Testing it

- `test/hero-protocol.test.ts` — the submission validator and the selector guards (`npx vitest run`).
- `tests/hero_journey.py` — the click-through, in a real browser against the running console, with
  every state-changing endpoint stubbed at the network layer, so it proves what the landing sends
  without starting an engagement.
- `tests/hero_layout.py` — layout measurements across window sizes: overlap, clipping, overflow,
  click-blocking, text contrast.
