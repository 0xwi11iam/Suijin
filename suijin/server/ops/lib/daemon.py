"""Daemon runner — engagements that outlive the console.

The `suijin` console owns the interactive TUI; a daemon engagement runs
in a DETACHED child process (new session, stdio to a run log) so closing
the console never kills it. The console talks to it through a durable
control record and the engagement's own artifacts:

  start   suijin daemon start "<objective>" [--resume X] [--set k=v ...]
  list    suijin ps                      (or: suijin daemon list)
  attach  suijin attach <id>             live-follow + guidance + /stop
  stop    suijin stop <id>               graceful SIGTERM (full-save)

The child reuses the exact production runner (run_red_team_async) with
autonomy=full by default: SIGTERM from `stop` triggers the clean
stop-with-full-save path (bundle + session + report), never a kill.
Attach writes operator guidance to the SAME seam the TUI uses
(state/live_guidance.md — consumed once per think turn) and tails
events.jsonl, so the journal stays the single source of truth.

Records live at <workspace>/daemon/<id>.json (atomic tmp+replace writes;
one writer per record = the child; the console only reads).
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

#: statuses that mean "the child is still (or should be) alive"
LIVE_STATUSES = ("spawning", "running")
#: how long `stop` gives the child before reporting it still shutting down
STOP_GRACE = 30.0
#: config a daemon engagement runs with, unless the operator pinned the key
#: explicitly (a daemon is unattended: SIGTERM must take the clean
#: stop-with-full-save path — bundle + session + report — never a kill).
#: NOT applied via setdefault: the operator's config.json pins these keys
#: (autonomy: "" is present-but-disabled), so presence never implies intent.
DAEMON_DEFAULTS = {"autonomy": "full"}

#: Popen handles for children THIS process spawned, so liveness checks can
#: reap them (a console that outlives its daemons would otherwise fill the
#: process table with zombies, and os.kill(pid, 0) answers for those).
_SPAWNED: dict[int, subprocess.Popen] = {}


def _ws_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    return WORKSPACE_DIR


def _project_dir() -> Path:
    """The repo root — the child's cwd so `-m suijin.main` resolves even
    in a source checkout (installed environments ignore cwd)."""
    from suijin.modules.platform.lib.workspace import PROJECT_DIR

    return PROJECT_DIR


def daemon_dir() -> Path:
    d = _ws_path() / "daemon"
    with contextlib.suppress(OSError):
        d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _slug(text: str) -> str:
    import re

    words = re.sub(r"[^a-zA-Z0-9 ]", " ", str(text or "engagement")).split()
    return ("_".join(w.lower() for w in words[:4]) or "engagement")[:40]


def _sanitize(config: dict) -> dict:
    """Mask secret-looking keys for the operator-visible record. Numeric
    values are never secrets (max_tokens: 4096 must stay readable) — the
    record is a diagnostic, not a vault."""
    out = {}
    for k, v in dict(config or {}).items():
        secret_ish = any(s in str(k).lower() for s in ("key", "token", "secret", "password"))
        if secret_ish and isinstance(v, (str, dict, list)):
            out[k] = "***stripped***" if v else v
        else:
            out[k] = v
    return out


def coerce_setting(value: str):
    """--set k=v: digits become ints, true/false become bools, else str."""
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return raw


# ── durable control records (atomic tmp+replace, one writer per record) ──


def _record_path(rid: str) -> Path:
    return daemon_dir() / f"{rid}.json"


def new_id(objective: str) -> str:
    return f"d_{_stamp()}_{_slug(objective)}"


def write_record(rec: dict) -> Path | None:
    """Atomically persist a record. Best-effort: never raises."""
    rid = str(rec.get("id") or "").strip()
    if not rid:
        return None
    path = _record_path(rid)
    tmp = path.with_suffix(".json.tmp")
    try:
        daemon_dir().mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(rec, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return None
    return path


def _read_record(rid: str) -> dict | None:
    p = _record_path(rid)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def update_record(rid: str, **fields) -> dict | None:
    """Add/overwrite fields on a record. Best-effort; returns the record."""
    rec = dict(_read_record(rid) or {"id": rid})
    rec["id"] = rid
    rec.update(fields)
    write_record(rec)
    return rec


def _records() -> list[dict]:
    """Every control record. `.cfg.json` (the child's override file) and
    `.tmp` (in-flight atomic writes) are NOT records — globbing them would
    put phantom rows in ps and let resolve_id match a config file."""
    out = []
    with contextlib.suppress(OSError):
        for p in sorted(daemon_dir().glob("*.json")):
            if p.name.endswith(".cfg.json") or p.name.endswith(".tmp"):
                continue
            rec = _read_record(p.stem)
            # a record without an id is a stray file, not an engagement
            if rec and str(rec.get("id") or "").strip():
                out.append(rec)
    return out


def is_live(rec: dict) -> bool:
    """A record is live when its status says so AND its pid is alive."""
    return str(rec.get("status") or "") in LIVE_STATUSES and actor_alive(int(rec.get("pid") or 0))


def actor_alive(pid: int) -> bool:
    """pid liveness without signalling the process (os.kill(pid, 0)).
    A zombie still answers — so a child we spawned is reaped first
    (otherwise a long-lived console accumulates zombies and every liveness
    probe lies)."""
    if int(pid or 0) <= 0:
        return False
    proc = _SPAWNED.get(int(pid))
    if proc is not None and proc.poll() is not None:
        _SPAWNED.pop(int(pid), None)
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def resolve_id(ref: str) -> str | None:
    """Resolve a daemon ref: exact id, id-prefix, or objective substring
    (records sorted newest-first so a substring picks the latest)."""
    ref = str(ref or "").strip().strip("'\"")
    if not ref:
        return None
    recs = sorted(_records(), key=lambda r: str(r.get("started_at", "")), reverse=True)
    for r in recs:
        if str(r.get("id")) == ref:
            return r["id"]
    for r in recs:
        if str(r.get("id", "")).startswith(ref):
            return r["id"]
    low = ref.lower()
    for r in recs:
        if low in str(r.get("objective", "")).lower():
            return r["id"]
    return None


def _cfg_path(rid: str) -> Path:
    return _record_path(rid).with_suffix(".cfg.json")


def _log_tail(path: str | Path, lines: int = 6) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = [r for r in text.splitlines() if r.strip()]
    return "\n    ".join(rows[-lines:])


# ── spawn (detached child: new session, stdio → run log) ───────────────


def child_command(rid: str, cfg_path: Path) -> list[str]:
    """The detached child: the same python, the normal suijin entry, the
    internal `daemon-run` verb (main.py dispatches known verbs to the
    CLI, which routes to the child entry)."""
    return [sys.executable, "-m", "suijin.main", "daemon-run", "--id", rid, "--config", str(cfg_path)]


def start_daemon(
    objective: str,
    overrides: dict | None = None,
    resume_ref: str = "",
    child_argv: list[str] | None = None,
    environ: dict | None = None,
    confirm_seconds: float = 0.0,
) -> dict:
    """Launch one detached engagement; returns its record.

    The child completes the record as it boots (engagement/state/events
    paths) and lands the final status on exit. `child_argv` exists for
    tests (a stub child that speaks the same protocol)."""
    objective = str(objective or "").strip()
    if not objective:
        raise ValueError("objective required")

    # the operator's explicit overrides ONLY — daemon defaults are applied
    # by the child for keys the operator did not pin (see DAEMON_DEFAULTS)
    overrides = {k: v for k, v in dict(overrides or {}).items() if v is not None}

    rid = new_id(objective)
    run_log = daemon_dir() / f"{rid}.run.log"
    rec = {
        "id": rid,
        "objective": objective,
        "pid": 0,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "status": "spawning",
        "engagement": "",
        "state_dir": "",
        "events": "",
        "bundle": "",
        "exit_reason": "",
        "exit_code": None,
        "finished_at": None,
        "resume_ref": str(resume_ref) if resume_ref else None,
        "set": _sanitize(overrides),
        "daemon_defaults": dict(DAEMON_DEFAULTS),
        "log": str(run_log),
    }
    write_record(rec)

    with contextlib.suppress(OSError):
        _cfg_path(rid).write_text(json.dumps(overrides, indent=1, default=str), encoding="utf-8")

    argv = list(child_argv) if child_argv else child_command(rid, _cfg_path(rid))
    env = dict(os.environ)
    env["SUIJIN_DAEMON_ID"] = rid
    if environ:
        env.update({str(k): str(v) for k, v in environ.items()})

    try:
        with open(run_log, "ab") as out:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # detaches from the controlling terminal
                close_fds=True,
                cwd=str(_project_dir()),
                env=env,
            )
    except OSError as e:
        update_record(rid, status="failed-to-spawn", exit_reason=str(e), finished_at=_now())
        return dict(_read_record(rid) or rec)

    rec.update({"pid": proc.pid, "status": "running"})
    _SPAWNED[proc.pid] = proc  # reap on liveness checks
    write_record(rec)

    if confirm_seconds > 0:
        _confirm_booted(rid, confirm_seconds)
    return dict(_read_record(rid) or rec)


def _confirm_booted(rid: str, seconds: float) -> None:
    """Wait briefly for the child to prove itself: it records its own pid
    and the engagement paths, or it dies. A dead-on-arrival child is
    marked (never left as a ghost 'running' record) — but a child that
    already landed a FINAL record (a fast run that completed inside the
    window) keeps it: its exit is the success, not a boot failure."""
    deadline = time.time() + max(0.0, seconds)
    rec = {}
    while time.time() < deadline:
        rec = _read_record(rid) or {}
        if int(rec.get("pid") or 0) != 0 and str(rec.get("engagement") or ""):
            return  # fully booted
        if str(rec.get("status") or "") not in LIVE_STATUSES:
            return  # the child already finished on its own — leave it be
        if not actor_alive(int(rec.get("pid") or 0)):
            break
        time.sleep(0.1)
    if not actor_alive(int(rec.get("pid") or 0)):
        update_record(rid, status="dead", exit_reason="child exited during boot")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── the detached child ─────────────────────────────────────────────────


def explicit_overrides(rid: str) -> dict:
    """Only what the operator pinned for THIS run (--set / the cfg file
    beside the record). Keys absent here get the daemon defaults."""
    with contextlib.suppress(OSError, ValueError):
        p = _cfg_path(rid)
        if p.is_file():
            return {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if v is not None}
    return {}


def load_run_config(rid: str) -> dict:
    """The child's effective config = operator config + daemon defaults
    for UNPINNED keys + this run's explicit overrides. The record itself
    stays sanitized (secrets live in the cfg file, never the record)."""
    from suijin.modules.platform.lib.config_loader import load_config

    cfg = dict(load_config() or {})
    explicit = explicit_overrides(rid)
    for key, val in DAEMON_DEFAULTS.items():
        if key not in explicit:
            cfg[key] = val
    cfg.update(explicit)
    return cfg


def engagement_watcher(watch: dict, rid: str) -> None:
    """Mirror the runner's set_engagement() into the record so ps/attach
    know the engagement/events/state paths the moment they exist. No
    runner hook: the daemon only shadows the workspace module global."""
    from suijin.modules.platform.lib.workspace import engagement_dir

    while not watch["stop"].is_set():
        try:
            d = engagement_dir()
            if d.name != "_default":
                update_record(rid, engagement=str(d), state_dir=str(d / "state"), events=str(d / "events.jsonl"))
                return
        except Exception:  # noqa: BLE001 — the watcher is best-effort
            pass
        time.sleep(0.2)


def preflight_resume(ref: str) -> str:
    """Cheap validation of a --resume ref BEFORE spawning: can this be
    resumed at all? Returns "" when fine, else the reason to show the
    operator. Mirrors the journal_resume / bundle guards without the
    (expensive) replay — the child does the full resolution."""
    ref = str(ref or "").strip().strip("'\"")
    if not ref:
        return ""
    from suijin.modules.agent.lib.event_log import read_events
    from suijin.modules.ops.lib.journal_resume import _is_resumable, _resolve_journal
    from suijin.modules.tools.lib.engagement_bundle import resolve_bundle

    with contextlib.suppress(Exception):
        bundle = resolve_bundle(ref)
        if bundle is not None:
            from suijin.modules.tools.lib.engagement_bundle import load_engagement

            loaded = load_engagement(bundle)  # hash-verifies: a tampered bundle fails HERE
            if not (loaded.get("graph_state") or {}):
                return "bundle has no state to resume (an early crash left it empty)"
            return ""
    with contextlib.suppress(Exception):
        log = _resolve_journal(ref)
        if log is not None and Path(log).is_file():
            evs = read_events(log)
            if not evs or evs[0].get("kind") != "session.start":
                return "not an engagement journal (no session.start)"
            if not _is_resumable(Path(log)):
                return "engagement already COMPLETED — refusing to resume a finished run"
            return ""
    return f"no resumable engagement, bundle or journal matches: {ref}"


def resolve_resume(ref: str) -> tuple[dict | None, str, str]:
    """Build a resume_state for a daemon resume. Accepts a .sje bundle OR
    an engagement/events ref (the same sources as `suijin load` /
    `suijin resume`). Returns (state, source-label, objective) — the
    objective comes from the SOURCE: a resume continues its own
    engagement, whatever the operator typed on the command line."""
    ref = str(ref or "").strip().strip("'\"")
    if not ref:
        return None, "", ""
    from suijin.modules.tools.lib.engagement_bundle import load_engagement, resolve_bundle, restore_side_files

    with contextlib.suppress(Exception):
        bundle = resolve_bundle(ref)
        if bundle is not None:
            loaded = load_engagement(bundle)
            state = dict(loaded.get("graph_state") or {})
            if not state:
                return None, "", ""  # an early-crash bundle: nothing to continue
            restore_side_files(bundle)
            state["completion_reason"] = None  # resumed = keep working
            return state, f"bundle:{bundle.name}", str(loaded.get("manifest", {}).get("objective") or "")

    from suijin.modules.agent.lib.event_log import read_events, replay
    from suijin.modules.ops.lib.journal_resume import _resolve_journal

    with contextlib.suppress(Exception):
        log = Path(_resolve_journal(ref))
        if log.is_file():
            evs = read_events(log)
            if evs and evs[0].get("kind") == "session.start" and "session.complete" not in {e.get("kind") for e in evs}:
                state = dict(replay(log) or {})
                if state.get("current_iteration") or state.get("messages"):
                    objective = str(state.get("original_objective") or evs[0].get("objective") or "")
                    state["completion_reason"] = None  # replayed runs run
                    return state, f"journal:{log.parent.name}", objective
    return None, "", ""


def run_engagement(config: dict, objective: str, resume_ref: str) -> dict:
    """Execute one engagement in-process (the child's core). Returns the
    final state. The runner never exits silently; KeyboardInterrupt
    (SIGTERM full-save), the CrashSaver backstops and the termination
    banner all land before this returns or re-raises."""
    import asyncio

    from suijin.modules.redteam.lib.redteamer import run_red_team_async

    resume_state, source, source_objective = resolve_resume(resume_ref)
    if resume_ref and not source:
        raise ValueError(f"cannot resolve resume source: {resume_ref}")
    if source_objective:
        objective = source_objective  # a resume continues ITS engagement
    return asyncio.run(run_red_team_async(config, objective, resume_state=resume_state)) or {}


def run_daemon_child(rid: str) -> int:
    """The detached child's main. Boots the record, runs the engagement,
    and ALWAYS lands a final record (status, bundle, exit code)."""
    # The TUI boots through init_runtime; this detached path never did,
    # so every boot-time semantic was silently missing here — most
    # visibly urllib3's InsecureRequestWarning suppression, which meant
    # every verify=False request printed a warning straight into the run
    # log the operator was following (and tore any live display).
    with contextlib.suppress(Exception):  # noqa: BLE001 — init must never block a run
        from suijin.modules.platform.lib.runtime import init_runtime

        init_runtime()
    rec = dict(_read_record(rid) or {})
    objective = str(rec.get("objective") or "")
    if not objective:
        return 2
    cfg = load_run_config(rid)
    update_record(rid, status="running", pid=os.getpid(), config=_sanitize(cfg))

    watch: dict = {"stop": threading.Event()}
    threading.Thread(target=engagement_watcher, args=(watch, rid), daemon=True).start()

    state: dict = {}
    code = 0
    status = "completed"
    reason = ""
    try:
        state = run_engagement(cfg, objective, str(rec.get("resume_ref") or ""))
    except KeyboardInterrupt:  # SIGTERM full-save (or a console KI)
        status, reason = "stopped", "operator stop"
    except Exception as e:  # noqa: BLE001 — one red line, never a silent exit
        status, reason = "crash", f"{type(e).__name__}: {e}"
        code = 1
        _log_crash(rid, objective, e)
    finally:
        watch["stop"].set()
        if status == "completed":
            reason = str(state.get("completion_reason") or "") if state else "run finished"
            if not state:
                status = "stopped"
        _finalize_record(rid, status, reason, code, state)
    return code


def _log_crash(rid: str, objective: str, exc: Exception) -> None:
    with contextlib.suppress(Exception):
        import traceback as _tb

        d = _ws_path() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "daemon_crash.log").open("a") as f:
            f.write(f"{_stamp()} daemon {rid} {objective[:80]}\n{_tb.format_exc()}\n")


def _finalize_record(rid: str, status: str, reason: str, code: int, state: dict) -> None:
    """Land the final record: engagement paths (if the watcher missed the
    race), the saved bundle, the classifier status and the ending time."""
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.workspace import engagement_dir

        d = engagement_dir()
        if d.name != "_default":
            update_record(rid, engagement=str(d), state_dir=str(d / "state"), events=str(d / "events.jsonl"))
    with contextlib.suppress(Exception):
        from suijin.modules.tools.lib.engagement_bundle import CRASH_SAVER

        if CRASH_SAVER.last_path is not None:
            update_record(rid, bundle=str(CRASH_SAVER.last_path))
    final = dict(_read_record(rid) or {"id": rid})
    final.update(
        {
            "status": status,
            "exit_reason": reason[:400],
            "exit_code": code,
            "finished_at": _now(),
            "iterations": int(state.get("current_iteration") or 0) if state else final.get("iterations"),
        }
    )
    write_record(final)


# ── console surface: ps / attach / stop ────────────────────────────────


def list_daemons() -> int:
    rows = sorted(_records(), key=lambda r: str(r.get("started_at", "")), reverse=True)
    if not rows:
        print('no daemon engagements — start one: suijin daemon start "<objective>"')
        return 0
    print(f"{'id':<32} {'status':<9} {'pid':>7}  objective")
    for r in rows:
        pid = int(r.get("pid") or 0)
        status = str(r.get("status") or "?")
        if status in LIVE_STATUSES and not actor_alive(pid):
            status = "dead"
        print(f"{str(r.get('id')):<32} {status:<9} {pid:>7}  {str(r.get('objective') or '')[:44]}")
    return 0


def stop_daemon(ref: str, wait: bool = True, grace: float = STOP_GRACE) -> int:
    """Graceful stop: SIGTERM → the full-auto runner's stop-with-full-save
    path (bundle + session + report). Never SIGKILLs."""
    rid = resolve_id(ref)
    if not rid:
        print(f"error: no such daemon engagement: {ref}")
        return 1
    rec = _read_record(rid) or {}
    pid = int(rec.get("pid") or 0)
    if not actor_alive(pid):
        print(
            f"{rid}: already finished ({rec.get('status') or 'dead'})"
            + (f" — bundle: {rec.get('bundle')}" if rec.get("bundle") else "")
        )
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"error: could not signal {rid} (pid {pid}): {e}")
        return 1
    if not wait:
        print(f"{rid}: stopping (SIGTERM → full save)")
        return 0
    deadline = time.time() + max(0.0, grace)
    while time.time() < deadline:
        current = _read_record(rid) or rec
        if not actor_alive(pid):
            print(
                f"{rid}: stopped ({current.get('status')})"
                + (f" — saved: {current.get('bundle')}" if current.get("bundle") else "")
            )
            return 0
        if not is_live(current):
            # the child landed its final record; the pid is just exiting
            print(
                f"{rid}: stopped ({current.get('status')})"
                + (f" — saved: {current.get('bundle')}" if current.get("bundle") else "")
            )
            return 0
        time.sleep(0.2)
    print(f"{rid}: still shutting down (pid {pid}) — the journal is live; check: suijin attach {rid}")
    return 0


def tail_events(events_path, offset: int) -> tuple[list[dict], int]:
    """New event records past the byte offset; a torn tail is dropped."""
    p = Path(events_path)
    if not p.is_file():
        return [], offset
    try:
        data = p.read_bytes()[offset:]
    except OSError:
        return [], offset
    out: list[dict] = []
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8", errors="ignore"))
            if isinstance(rec, dict) and rec.get("kind"):
                out.append(rec)
        except ValueError:
            continue
    return out, offset + len(data)


def render_event(rec: dict, names: dict | None = None) -> str | None:
    """One event → one console line (or None to stay quiet). `names` maps
    tool.call ids to tool names (tool.result records carry only the id)."""
    names = names if names is not None else {}
    kind = rec.get("kind")
    if kind == "session.start":
        return f"[boot] {str(rec.get('objective'))[:120]}"
    if kind == "iteration":
        return f"  iter {rec.get('n')}" + (f" · {rec.get('phase')}" if rec.get("phase") else "")
    if kind == "phase.transition":
        return f"  phase → {rec.get('to')}"
    if kind == "tool.call":
        names[str(rec.get("id"))] = str(rec.get("name") or "?")
        return None
    if kind == "tool.result":
        name = names.get(str(rec.get("id")), "?")
        if rec.get("ok"):
            return f"  ✓ {name}"
        return f"  ✗ {name}" + (f" ({rec.get('error_kind')})" if rec.get("error_kind") else "")
    if kind == "guidance.delivered":
        return f"  [operator] {str(rec.get('text'))[:120]}"
    if kind == "assistant.message":
        text = " ".join(str(rec.get("content") or "").split())[:160]
        return f"  agent: {text}" if text else None
    if kind == "session.complete":
        return f"  [complete] {str(rec.get('reason'))[:160]}"
    if kind == "session.interrupt":
        return f"  [interrupt] {str(rec.get('reason'))[:160]}"
    if kind == "usage":
        return f"  usage: {rec.get('input_tokens', 0)} in / {rec.get('output_tokens', 0)} out · ${float(rec.get('cost_usd') or 0.0):.4f}"
    return None


def write_guidance(rec: dict, line: str) -> Path | None:
    """Write operator guidance to the daemon engagement's live_guidance.md
    (the SAME seam the TUI uses — the think node consumes it once)."""
    base = str(rec.get("state_dir") or "") or str(rec.get("engagement") or "")
    if not base:
        return None
    p = Path(base) / "live_guidance.md"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(str(line).strip() + "\n")
    except OSError:
        return None
    return p


#: what an attached console can do vs. what needs the foreground TUI
ATTACH_HELP = """attached console (a detached run — the agent keeps working if you leave):
  <any text>   queue guidance for the agent's next think turn
  /state       phase, iteration, todos, open questions (last journal snapshot)
  /findings    confirmed findings so far
  /cost        tokens + spend (journal usage records)
  /note <text> write an engagement note
  /bundle      path of the saved .sje (once it exists)
  /stop        graceful stop with a full save (SIGTERM)  ·  /quit same
  /detach      leave — the daemon keeps running
  Ctrl+C       same as /detach (the RUN is never killed by leaving)
  (in-process commands like /compact or /approvals need `suijin tui` —
   they act on a live graph this console does not own)"""


def _journal_state(events_path) -> dict:
    """The latest state.snapshot from the journal (empty when none)."""
    state: dict = {}
    with contextlib.suppress(Exception):
        recs, _ = tail_events(events_path, 0)
        for rec in recs:
            if rec.get("kind") == "state.snapshot":
                state = dict(rec)
    return state


def _journal_usage(events_path) -> tuple[int, int, float]:
    """(input, output, cost) summed from the journal's usage records."""
    tin = tout = 0
    cost = 0.0
    with contextlib.suppress(Exception):
        recs, _ = tail_events(events_path, 0)
        for rec in recs:
            if rec.get("kind") == "usage":
                tin += int(rec.get("input_tokens") or 0)
                tout += int(rec.get("output_tokens") or 0)
                cost += float(rec.get("cost_usd") or 0.0)
    return tin, tout, cost


def _cmd_state(rec: dict) -> None:
    state = _journal_state(rec.get("events") or "")
    if not state:
        print("no state snapshot yet")
        return
    todos = state.get("todo_list") or []
    questions = state.get("pending_questions") or []
    print(
        f"phase: {state.get('current_phase', '?')}  ·  iteration: {state.get('current_iteration', 0)}"
        f"  ·  findings: {len(state.get('findings') or [])}  ·  todos: {len(todos)}"
    )
    for todo in todos[:8]:
        text = todo.get("task") if isinstance(todo, dict) else todo
        print(f"  - {str(text)[:100]}")
    if questions:
        print(f"open questions ({len(questions)}):")
        for q in questions[:4]:
            qq = q.get("question") if isinstance(q, dict) else q
            print(f"  ? {str(qq)[:120]}")


def _cmd_findings(rec: dict) -> None:
    findings = _journal_state(rec.get("events") or "").get("findings") or []
    if not findings:
        print("no findings recorded yet")
        return
    for f in findings:
        if isinstance(f, dict):
            print(f"  - [{f.get('severity', '?')}] {str(f.get('title') or f.get('name') or f)[:110]}")
        else:
            print(f"  - {str(f)[:110]}")


def _cmd_cost(rec: dict) -> None:
    tin, tout, cost = _journal_usage(rec.get("events") or "")
    print(f"tokens: {tin} in / {tout} out   ·   spend: ${cost:.4f}")


def _cmd_note(rec: dict, text: str) -> None:
    base = str(rec.get("engagement") or "")
    if not base:
        print("engagement not booted yet — no note written")
        return
    notes = Path(base) / ".notes"
    with contextlib.suppress(OSError):
        notes.mkdir(parents=True, exist_ok=True)
        stamp = _stamp()
        with (notes / f"operator_{stamp}.md").open("a", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
        print(f"note written: {(notes / f'operator_{stamp}.md').name}")
        return
    print("note failed (workspace not writable?)")


def _print_final(rec: dict) -> None:
    status = str(rec.get("status") or "unknown")
    reason = str(rec.get("exit_reason") or "")
    if status == "completed":
        print(f"\n[ENGAGEMENT COMPLETE] {reason}" if reason else "\n[ENGAGEMENT COMPLETE]")
        if rec.get("bundle"):
            print(f"bundle: {rec['bundle']}")
        elif rec.get("events"):
            print(f"journal: {rec['events']} — resume: suijin resume {Path(str(rec['events'])).parent.name}")
    else:
        print(f"\n[{status.upper()}] {reason}")
        if rec.get("bundle"):
            print(f"saved: {rec['bundle']} — resume: suijin load {Path(str(rec['bundle'])).name}")


def attach_daemon(ref: str, poll: float = 0.2, heartbeat: float = 8.0) -> int:
    """Live-follow a daemon engagement: tail events.jsonl, forward
    operator guidance, /stop for a graceful save. Detaching never stops
    the run (the daemon is the point)."""
    rid = resolve_id(ref)
    if not rid:
        print(f"error: no such daemon engagement: {ref}")
        return 1
    rec = _read_record(rid) or {}
    print(f"attached: {rid} — {str(rec.get('objective') or '')[:100]}")
    print("type a line to queue guidance  ·  /help  ·  /state  ·  /stop  ·  /detach")

    stdin_fd = None
    with contextlib.suppress(AttributeError, OSError, ValueError):
        stdin_fd = sys.stdin.fileno() if sys.stdin is not None and sys.stdin.isatty() else None

    # Ctrl+C while following = leave, NEVER kill: the run is detached and
    # outliving this console is the entire point of the split.
    try:
        return _attach_loop(rid, stdin_fd, poll, heartbeat)
    except KeyboardInterrupt:
        print("\ndetached — the daemon keeps running")
        return 0


def _attach_loop(rid, stdin_fd, poll, heartbeat) -> int:
    """The follow loop: tail the journal, service the console, end when
    the run does. (Ctrl+C is handled one frame up, as a detach.)"""
    offset = 0
    names: dict[str, str] = {}
    last_line = time.time()
    last_iter = "?"
    while True:
        rec = _read_record(rid) or {}
        events = str(rec.get("events") or "")
        if events:
            recs, offset = tail_events(events, offset)
            for r in recs:
                line = render_event(r, names)
                if line:
                    print(line)
                if r.get("kind") == "iteration":
                    last_iter = str(r.get("n"))
            if recs:
                last_line = time.time()

        if stdin_fd is not None:
            try:
                ready, _, _ = select.select([stdin_fd], [], [], 0.0)
            except (OSError, ValueError):
                ready = []
            if ready:
                try:
                    line = sys.stdin.readline() or ""
                except KeyboardInterrupt:
                    print("\ndetached — the daemon keeps running")
                    return 0
                except Exception:  # noqa: BLE001 — a broken read must not kill the follower
                    line = ""
                cmd = line.strip()
                low = cmd.lower()
                if low in ("/stop", "/quit"):
                    return stop_daemon(rid)
                if low == "/bundle":
                    print((_read_record(rid) or {}).get("bundle") or "no bundle yet")
                elif low == "/state":
                    _cmd_state(rec)
                elif low == "/findings":
                    _cmd_findings(rec)
                elif low == "/cost":
                    _cmd_cost(rec)
                elif low in ("/help", "/?"):
                    print(ATTACH_HELP)
                elif low.startswith("/note"):
                    _cmd_note(rec, cmd.partition(" ")[2] or "(empty note)")
                elif low in ("/detach", "/leave", "q"):
                    print("detached — the daemon keeps running")
                    return 0
                elif low.startswith("/"):
                    print(f"unknown command {cmd.split()[0]} — /help lists what this console can do")
                elif cmd:
                    p = write_guidance(rec, cmd)
                    if p is not None:
                        print(f"> guidance queued for the next think turn ({p.name})")
                    else:
                        print("> engagement not booted yet — retry in a second")

        if not is_live(rec):
            _print_final(_read_record(rid) or rec)
            return 0
        if heartbeat > 0 and time.time() - last_line >= heartbeat:
            # never let the follower look hung: the agent is usually just
            # waiting on a model, which is slow by nature
            print(f"  … working (iter {last_iter}) · last event {int(time.time() - last_line)}s ago")
            last_line = time.time()
        time.sleep(poll)


# ── CLI entry (suijin daemon start) ────────────────────────────────────


def launch_and_attach(
    objective: str,
    overrides: dict | None = None,
    resume_ref: str = "",
    attach: bool = True,
    wait: float = 0.0,
) -> int:
    """Start one detached engagement and (by default) follow it — the
    single path behind `suijind` and the default `suijin` launch. Fail
    fast on a bad --resume ref, report a dead-on-arrival child, and only
    attach once the record is real. Returns a process exit code."""
    resume_ref = str(resume_ref or "").strip()
    if resume_ref:
        problem = preflight_resume(resume_ref)
        if problem:
            print(f"error: cannot resume — {problem}")
            return 1
    rec = start_daemon(objective, overrides=overrides, resume_ref=resume_ref, confirm_seconds=max(wait, 0.0))
    rid = str(rec.get("id") or "")
    status = str(rec.get("status") or "")
    if status in ("failed-to-spawn", "dead"):
        print(f"error: daemon {rid} {status} — {rec.get('exit_reason') or ''}")
        tail = _log_tail(rec.get("log") or "")
        if tail:
            print(f"  run log:\n    {tail}")
        return 1
    print(f"started {rid} (pid {rec.get('pid')}) — detached; this console can exit safely")
    print(f"  follow: suijin attach {rid}   ·   list: suijin ps   ·   stop: suijin stop {rid}")
    print('  (want the live Rich TUI with the graph in this console? suijin tui "<objective>")')
    if not attach:
        return 0
    print()
    return attach_daemon(rid)


def run_daemon_start(args) -> int:
    """`suijin daemon start "<objective>" [--resume X] [--set k=v ...]`."""
    overrides = {}
    for kv in getattr(args, "set", []) or []:
        if "=" in str(kv):
            k, v = str(kv).split("=", 1)
            if k.strip():
                overrides[k.strip()] = coerce_setting(v)
    resume_ref = str(getattr(args, "resume", "") or "").strip()
    # fail fast, BEFORE spawning: a bad --resume must not leave a child
    # that crashes a second later with the same news
    problem = preflight_resume(resume_ref) if resume_ref else ""
    if problem:
        print(f"error: cannot resume — {problem}")
        return 1
    rec = start_daemon(
        str(getattr(args, "objective", "") or "").strip(),
        overrides=overrides,
        resume_ref=str(getattr(args, "resume", "") or "").strip(),
        confirm_seconds=float(getattr(args, "wait", 0.0) or 0.0),
    )
    status = str(rec.get("status") or "")
    if status in ("failed-to-spawn", "dead"):
        print(f"error: daemon {rec.get('id')} {status} — {rec.get('exit_reason') or ''}")
        tail = _log_tail(rec.get("log") or "")
        if tail:
            print(f"  run log:\n    {tail}")
        return 1
    print(f"started {rec.get('id')} (pid {rec.get('pid')}) — detached; the console can exit safely")
    print(f"  attach: suijin attach {rec.get('id')}")
    print("  list:   suijin ps")
    print(f"  stop:   suijin stop {rec.get('id')}   (graceful full-save)")
    return 0
