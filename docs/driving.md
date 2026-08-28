# Driving the TUI — the interactive review rig

The AI (or operator) can run a REAL engagement under a REAL PTY, drive
it with raw keystrokes, and read actual frames back. Every TUI bug gets
reproduced here BEFORE the fix lands.

## Launch

```
.venv/bin/python scripts/tui_drive.py --target lab:blue_target \
    [--provider real|fake] [--max-iters N] [--objective "..."] \
    [--ws DIR] [--slow SECS]
```

- `--target`: `lab:blue_target` | `lab:hill_ctf` (auto-booted on a free
  port) or any `http://host:port`. Labs by default; external targets
  only when the operator explicitly provides one.
- `--provider fake`: scripted LLM (free, deterministic — streams
  reasoning+content, executes real tool calls). `real`: actual zai
  streaming, capped by `--max-iters` (default 8 — cents per review).
- `--slow SECS`: fake provider holds each turn N seconds — for testing
  pause/resume while the "LLM" is stuck mid-turn.
- `--ws DIR`: isolated `SUIJIN_WORKSPACE` for reproducible runs.

## Drive

```
printf 'focus on /admin\r'  > /tmp/suijin_drive/in.pipe   # type + Enter
printf '\x1b\x1b'           > /tmp/suijin_drive/in.pipe   # ESC ESC (pause)
printf '\t'                 > /tmp/suijin_drive/in.pipe   # Tab (mode cycle)
printf '/cost\r'            > /tmp/suijin_drive/in.pipe   # pause command
printf '/quit\r'            > /tmp/suijin_drive/in.pipe   # end + save .sje
```

## Read

```
.venv/bin/python scripts/tui_drive.py --screen --lines 50   # last screen (ANSI-stripped)
tail -c 4000 /tmp/suijin_drive/out.log                      # raw frames
cat /tmp/suijin_drive/exit.json                             # outcome
cat /tmp/suijin_reader_crash.log 2>/dev/null                # input-reader crashes (always logged)
```

`SUIJIN_DRIVE_DEBUG=1` on launch also logs every keystroke the reader
sees (`/tmp/rig_keys.log`) — for delivery-path debugging.

## Ground truth beyond the screen

`outputs/logs/ui_crash.log` (render guard), `outputs/logs/engagement.log`
(provider warnings), `outputs/sessions/*.json`, the `.sje` bundle, and
`/tmp/rig_keys.log` under debug.

## The loop

reproduce in the rig → fix → re-drive the same sequence → prove → add
the sequence to `tests/redteam/test_tui_pty.py` (marked slow) so it can
never regress silently.

## Bugs this rig caught (history)

- `apply_key` dropped the buffer on Enter — every typed prompt vanished
- TextIOWrapper buffering swallowed the second ESC — the pause chord
  never fired for fast/one-write double-ESC
- The reader thread died silently on its first keystroke after the
  `_pump` split (unbound `buf`)
- The pause session needed the PauseContext before the main thread
  landed, or pause commands fell into a queue nobody consumed
