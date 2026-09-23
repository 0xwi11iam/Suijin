# The Suijin Rebuild — server/client architecture

> Ratified 2026-09-23. Adapts the opencode model (session server + subscribing
> clients + persisted event records) to suijin's Python modular kernel.

## Regions

```
suijin/kernel/     the modular kernel (unchanged contract: stdlib-pure)
suijin/server/     everything that IS the product: the runner, tools,
                   providers, memory workers, event log, logging subprocess
suijin/client/     the Rich TUI (rendering only — a subscriber of events,
                   a sender of control ops)
```

The client renders what the server publishes; the server never imports the
client. Control flows one way (client → server ops), events flow one way
(server → clients).

## The server

- **SessionRunner** — the loop core extracted from redteamer: provider
  stream → decisions → tool jobs → results. Publishes typed
  `EngagementEvent`s. No UI imports, no console objects.
- **EventLog** — append-only `events.jsonl` per engagement, RECORDS ONLY
  (completed messages/tool results; deltas stay in-memory). THE truth:
  resume = replay. `.sje` becomes a derived export.
- **Event bus** — in-process fan-out first (log writer, TUI adapter,
  audit); the gateway subscribes for remote clients.
- **Memory workers** — librarian / recall-digest / KB-FTS as processes
  consuming `tool.result` events. No GIL contention; restartable.
- **Tool executor** — one process per engagement: typed jobs in,
  `tool.result` records out, progress events for long jobs, cancel
  honored between steps. Shell sessions are executor-owned state.
- **Logging subprocess** — contained by the server, writes
  `log/engagement.log` per engagement from the bus.

## Event schema v1

```jsonc
{"v":1,"seq":N,"ts":"...","kind":"session.start","objective":...,"provider":...}
{"kind":"iteration","n":2,"phase":"informational"}
{"kind":"phase.transition","from":"...","to":"..."}
{"kind":"assistant.message","content":"{...decision json...}"}
{"kind":"tool.call","id":"t7","name":"http_request","args":{...}}
{"kind":"tool.result","id":"t7","ok":true,"error_kind":null,"duration_ms":412,"output":"...<=64k...","truncated":false}
{"kind":"guidance.delivered","text":"...","source":"operator"}
{"kind":"usage","input_tokens":...,"output_tokens":...,"cost_usd":...,"priced":true}
{"kind":"session.complete","reason":"..."} / {"kind":"session.interrupt","kind":"pause|stop"}
```

Single writer, line-buffered, atomic lines (torn tails are dropped by the
reader). Cancellation is a token (`pause|resume|stop`) checked at await
points — KeyboardInterrupt maps to a token at the outermost frame only.

## Workspace v3 (per engagement)

```
engagements/<id>/
  home/          the agent's USERLAND — a small Linux-feeling home:
                 Downloads/ Documents/ .config/ ... write-jail root
  log/           engagement.log (from the logging subprocess)
  state/         scratchpad, guidance, coverage ...
  memory/        the librarian ledger
  reports/  exploits/  audit_trails/
  events.jsonl   THE truth
```

No archive/, no exports/, no outputs/, no blue_config. `.sje` bundles live
in the engagement folder. **Confinement**: every agent write resolves
inside `engagements/<id>/` — host shell reads allowed, writes jailed at
the tool layer.

## Migration order (each step ships green, one commit each)

1. event schema + events.jsonl writer (write-only)
2. replay() — state from the log; parity-tested
3. event bus + in-process TUI adapter
4. cancel tokens (pause-crash class dies)
5. folder split: kernel / server / client
6. workspace v3 + home userland + write jail
7. logging subprocess + gateway live feed
