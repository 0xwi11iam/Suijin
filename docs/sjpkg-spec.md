# The .sj? Package Format — author guide

Share suijin capabilities as single files, person-to-person: Discord, a
release, a USB stick, an airgap. No code reading required on either side.

## The three kinds

| Extension | Kind | Source shape | Installs to |
|---|---|---|---|
| `.sjm` | module pack | `manifest.json` + `main.py` (+ `skill.md`) | `~/.suijin/modules/<id>/` |
| `.sja` | addon | bare `main.py` (zero boilerplate) | `suijin/addons/<id>/` |
| `.sjp` | kernel plugin | `plugin.json` (+ `lib/main.py`) | `~/.suijin/modules/<id>/` (tier-gated) |

## Build (author side)

```bash
suijin pack build ./my-pack --note "What it does, from you" --author yourname
# -> ./built/my-pack-1.0.sjm  (+ its sha256 printed — publish both)
```

The builder validates the pack, extracts the tool table from your
`main.py` public functions (docstring = description, signature = args),
scans it with the safety scanner, and seals everything with SHA-256 sums.
If your `manifest.json` lacks a valid `tools` map, it is auto-filled from
your actual code — you cannot ship an invalid manifest.

## Install (user side)

```bash
suijin install headerpeek-1.0.sjm
```

The wizard shows: who built it and when, your dev note, the safety-scan
verdict, the exact tools it adds, any external binaries it calls. Confirm,
and it boots on the next launch. Non-interactive installs require
`--yes`. `--allow-unsafe` overrides CRITICAL scan findings — know what
you're doing.

## The safety scanner

Every install re-scans source from scratch (embedded reports are advisory
only) using pure AST analysis — the scanner never executes payload code.
CRITICAL findings (hidden `eval`/`exec`, hardcoded secrets, encoded-blob
obfuscation, built-in tool-name shadowing) refuse the install by default.
Warnings (network use, process spawning, import-time effects) are shown
on the card. Declare the binaries you call in your manifest's
`external_binaries` — declared spawns are honest metadata; undeclared
ones are flagged.

## Seals and guards

- `SHA256SUMS` inside every archive — any in-transit edit refuses loudly
- Path traversal, symlink entries, and zip bombs are refused outright
- A public function named like a built-in tool (`http_request`, `nmap`, …)
  is REFUSED — the loader's tool namespace is flat and a shadow would be
  a supply-chain takeover
- `tier: core` plugins cannot be installed — community max is `recommended`

## Container layout (for tooling authors)

```
sjpkg.json    metadata: kind/id/version/author/built_at/dev_note/description/
              tools[]/external_binaries[]/tier/source_url/advisory_scan
SHA256SUMS    "<sha256>  <relpath>" per payload file
<payload>     the pack source itself
```

Format version: 1. Zero dependencies beyond the Python stdlib.

## Rent ledger (the deletion criterion)

Builds and installs are counted in `outputs/pack_stats.json`. If two
releases pass with zero ecosystem usage, this feature is removed — the
ecosystem code pays rent or it goes.
