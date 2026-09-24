"""Panic button — `suijin panic`. Stop everything Suijin started, now.

Kills Suijin-owned processes (TUI, web console, labs, spawned scanners),
clears blue-team live state in /tmp, and reports what it did. Every step
is best-effort: a panic command must never itself fail.
"""

from __future__ import annotations

import contextlib
import glob
import subprocess
import tempfile
from pathlib import Path

# Process patterns that are OURS to kill — deliberately narrow so panic
# never touches unrelated operator processes.
_PATTERNS = [
    "suijin/main.py",
    "suijin/cli.py ui",
    "suijin/lab/",
    "vulnerable_app.py",
    "suijin.main daemon-run",  # detached daemon engagements (see _stop_daemons)
]

# Live blue-team state in /tmp (constants are session-scoped by design).
_STATE_GLOBS = [
    "blue_kg.json",
    "blue_tarpit.json",
    "blue_honeypots.json",
    "blue_defend_traffic.jsonl",
    "blue_proxy_*.jsonl",
]


def _stop_daemons(dry_run: bool, lines: list[str]) -> None:
    """Detached daemon engagements run real tools — an emergency stop MUST
    reach them. They are stopped through the daemon seam (SIGTERM → the
    runner's full-save path), never a bare kill, so each lands a final
    record the operator can resume from. Best-effort by design."""
    with contextlib.suppress(Exception):
        from suijin.modules.ops.lib import daemon

        live = [r for r in daemon._records() if daemon.is_live(r)]
        if not live:
            lines.append("daemon engagements: none running")
            return
        if dry_run:
            lines.append(f"daemon engagements: would stop {len(live)} (full save)")
            return
        for rec in live:
            with contextlib.suppress(Exception):
                daemon.stop_daemon(str(rec.get("id") or ""), wait=False)
        lines.append(f"daemon engagements: SIGTERM → full save ({len(live)})")


def panic(dry_run: bool = False) -> str:
    """Kill our processes + clear live state. Returns a report."""
    lines: list[str] = []
    _stop_daemons(dry_run, lines)
    for pat in _PATTERNS:
        try:
            r = subprocess.run(["pkill", "-f", pat], capture_output=True, timeout=10)
            # pkill exit 0 = matched & signalled; 1 = nothing matched
            lines.append(f"processes '{pat}': {'signalled' if r.returncode == 0 else 'none running'}")
        except Exception as e:
            lines.append(f"processes '{pat}': skipped ({e})")
    removed = []
    for pattern in _STATE_GLOBS:
        for f in glob.glob(str(Path(tempfile.gettempdir()) / pattern)):
            if dry_run:
                removed.append(f"{f} (would remove)")
            else:
                try:
                    Path(f).unlink()
                    removed.append(f)
                except OSError:
                    pass
    if removed:
        lines.append("live state cleared:" + ("\n  " + "\n  ".join(removed) if len(removed) > 1 else f" {removed[0]}"))
    else:
        lines.append("live state: nothing to clear")
    head = "PANIC (dry-run)" if dry_run else "PANIC — all Suijin processes stopped"
    return "\n".join([head] + lines)
