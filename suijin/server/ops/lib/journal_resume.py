"""Journal resume — continue an interrupted engagement from its event log.

The event log (``engagements/<slug>/events.jsonl``) is the durable truth:
every decision, tool call/result, phase transition and a per-iteration
``state.snapshot`` (todos, findings, chain memory, targeting — the fields
a raw event stream cannot rebuild). ``suijin resume [slug]`` reconstructs
AgentState from the log via ``replay()`` and continues the run — no .sje
needed. The bundle stays as the export/portability format.

Refused when the engagement already completed (trailing ``session.complete``
— a replayed run MUST run) or the log has no ``session.start``.
"""

from __future__ import annotations

import contextlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from suijin.modules.agent.lib.event_log import read_events, replay


def _engagement_dirs() -> Iterator[Path]:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    engs = WORKSPACE_DIR / "engagements"
    if engs.is_dir():
        yield from sorted(engs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)


def _is_resumable(log: Path) -> bool:
    """True when an events.jsonl is a real unfinished engagement journal:
    starts with session.start and was never marked session.complete."""
    events = read_events(log)
    if not events or events[0].get("kind") != "session.start":
        return False
    return "session.complete" not in {e.get("kind") for e in events}


def resumable_engagements() -> list[dict]:
    """Engagements with a real, unfinished journal: session.start present,
    no trailing session.complete. Newest first. Never raises."""
    out: list[dict] = []
    for d in _engagement_dirs():
        log = d / "events.jsonl"
        with contextlib.suppress(Exception):
            if not log.is_file() or not _is_resumable(log):
                continue
            evs = read_events(log)
            st = log.stat()
            out.append(
                {
                    "slug": d.name,
                    "dir": d,
                    "events": len(evs),
                    "iteration": next((e.get("n") for e in reversed(evs) if e.get("kind") == "iteration"), 0),
                    "phase": next((e.get("phase") for e in reversed(evs) if e.get("kind") == "iteration"), ""),
                    "last_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                }
            )
    return out


def _resolve_journal(ref: str) -> Path:
    """A path to the events.jsonl, an engagement dir, or a slug."""
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    p = Path(str(ref)).expanduser()
    if p.is_file() and p.name == "events.jsonl":
        return p
    if p.is_dir():
        j = p / "events.jsonl"
        if j.is_file():
            return j
    slug = Path(str(ref).strip().strip("/")).name
    cand = WORKSPACE_DIR / "engagements" / slug / "events.jsonl"
    return cand


def resume_from_journal(ref: str | Path | None = None) -> int:
    """Reconstruct state from the journal and continue the engagement.
    Returns the process exit code. Callers must handle ValueError."""
    if not str(ref or "").strip():
        picked = _pick_resumable()
        if picked is None:
            return 1
        ref = str(picked["dir"])

    log = _resolve_journal(str(ref))
    if not log.is_file():
        raise ValueError(f"no engagement journal at: {log}")
    evs = read_events(log)
    if not evs or evs[0].get("kind") != "session.start":
        raise ValueError(f"not an engagement journal (no session.start): {log}")
    if "session.complete" in {e.get("kind") for e in evs}:
        raise ValueError(
            f"engagement already COMPLETED — refusing to resume a finished run (edit {log.parent.name} or start"
            " a new engagement)"
        )

    state = replay(log)
    if not state.get("current_iteration") and not state.get("messages"):
        raise ValueError(f"journal has no work to resume (empty replay): {log}")

    objective = state.get("original_objective") or evs[0].get("objective", "")
    state["completion_reason"] = None  # replayed runs run, never re-complete

    from suijin.modules.platform.lib.config_loader import load_config
    from suijin.modules.redteam.lib.redteamer import run_red_team

    config = load_config()  # operator SETTINGS ride live config.json (current wins)
    print(
        f"[resume] {log.parent.name} — iteration {state.get('current_iteration', 0)}, "
        f"phase {state.get('current_phase', '?')}, {len(state.get('messages', []))} message(s) from journal"
    )
    return int(run_red_team(config, objective, resume_state=state) or 0)


def _pick_resumable(limit: int = 10) -> dict | None:
    """Interactive picker over unfinished engagements (newest first)."""
    rows = resumable_engagements()[: max(1, limit)]
    if not rows:
        print("no unfinished engagements on disk — run `suijin engage`")
        return None
    if not sys.stdin.isatty():
        print(f"non-interactive: resuming newest engagement {rows[0]['slug']}")
        return rows[0]
    print()
    print("  Unfinished engagements (newest first):")
    for i, r in enumerate(rows, 1):
        print(f"   {i:>2}. {r['last_at']}  iter {r['iteration']} ({r['phase']})  {r['slug']}")
    print()
    try:
        raw = input("  resume which [1-10, path/slug, blank=1, q=quit]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None
    if not raw:
        return rows[0]
    if raw.lower() in ("q", "quit", "n"):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(rows):
        return rows[int(raw) - 1]
    log = _resolve_journal(raw)
    if log.is_file():
        for r in rows:
            if r["dir"] == log.parent:
                return r
    print(f"  no match for: {raw}")
    return None


def export_state_snapshot(log: Path) -> dict:
    """The last state.snapshot of a journal (raw, for diagnostics/debug).
    Empty when the journal has none."""
    state: dict = {}
    for ev in read_events(log):
        if ev.get("kind") == "state.snapshot":
            state = dict(ev)
            state.pop("v", None)
            state.pop("seq", None)
            state.pop("ts", None)
            state.pop("kind", None)
    return state


def _cli_list() -> int:
    rows = resumable_engagements()
    if not rows:
        print("no unfinished engagements on disk — run `suijin engage`")
        return 0
    print(f"{'LAST':<20} {'ITER':>5}  {'PHASE':<16} SLUG")
    for r in rows:
        print(f"{r['last_at']:<20} {r['iteration']:>5}  {r['phase']:<16} {r['slug']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="suijin resume", description="continue an engagement from its event log")
    ap.add_argument("ref", nargs="?", default=None, help="engagement slug, dir, or events.jsonl path (omit to pick)")
    ap.add_argument("--list", action="store_true", help="list unfinished engagements")
    args = ap.parse_args(argv)
    if args.list:
        return _cli_list()
    try:
        return resume_from_journal(args.ref)
    except ValueError as e:
        print(f"error: {e}")
        return 1
