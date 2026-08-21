# Suijin Desktop

A native desktop client for the Suijin core. Contains ZERO agent code —
it is a pure client of the gateway.

## Run it

```bash
# 1. start the core gateway (prints the session token)
suijin gateway

# 2. run the desktop app (dev mode)
cd desktop
npm install
npm run dev          # opens http://localhost:5174 — paste the token

# production build (Tauri native shell)
npm run tauri build  # -> Suijin.app + dmg in src-tauri/target/release/bundle/
```

Built artifacts (this machine, arm64): `dist-desktop/Suijin_0.1.0_aarch64.dmg`
(4 MB, UDZO). The .app embeds the frontend; connect it to a running
`suijin gateway` with the session token. If the dmg beautification step
(create-dmg) fails on your machine, the raw rw.*.dmg next to Suijin.app
converts with: `hdiutil convert <rw>.dmg -format UDZO -o out.dmg`.

## Architecture

```
[Tauri app — TypeScript/React]  <--WS /events + REST-->  [suijin gateway]  -->  [kernel ctx]
```

- **Types are generated**: `npm run gen` reads the gateway's OpenAPI and
  emits `src/lib/api-types.ts` — the UI's types ARE the gateway's schema;
  core and client cannot drift.
- **Live**: the WS stream tails agent audit trails, cost ticker, and
  HITL (approvals/questions) snapshots.
- **Security**: localhost-bound by default; per-boot bearer token
  (compare-digest); the only writes are explicit operator actions.

## Design system

Cold cyan/teal accent on near-black (radar instrument). Geist for UI,
Instrument Serif for display numerals, Fira Code in code blocks ONLY.
No purple, no mono UI, no eyebrow labels. Cyan is reserved for
LIVE/active moments.

Fonts are OFL-licensed via Fontsource (Geist, Instrument Serif, Fira
Code). Icons generated from the project's own logo art.
