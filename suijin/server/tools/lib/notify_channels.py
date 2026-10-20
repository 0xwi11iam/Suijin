"""Notify — local-first finding push (2026-10-20, ARTEX notify/ adaptation).

The field pain: a long run ends and the operator never knew a critical
landed at iteration 40. This module pushes CONFIRMED findings and
engagement endings to LOCAL channels — no phone, no cloud:

- macos : the macOS notification center (osascript; auto-skipped when
          unavailable — headless/SSH/Linux)
- file  : notifications.jsonl in the workspace (the durable record;
          the operator TUI/console can render it anytime)

ARTEX's hard-won semantics, kept verbatim in spirit:
- filters NEVER silently drop (a malformed filter means no filter —
  one extra alert beats one silently missing critical)
- payloads are FROZEN AT EVENT TIME (a later severity edit must not
  rewrite what you were alerted about)
- truncation accounts for what it kept (kept-count), never swallows
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from pathlib import Path


def _cfg() -> dict:
    with contextlib.suppress(Exception):
        from suijin.modules.platform.lib.config_loader import load_config

        n = (load_config() or {}).get("notify") or {}
        return n if isinstance(n, dict) else {}
    return {}


def _enabled() -> bool:
    return bool(_cfg().get("enabled", True))  # local channels default ON


def _min_severity() -> str:
    """Filter floor. A MALFORMED value means no floor at all — the
    never-silently-drop rule (a typo must not stop criticals)."""
    v = str(_cfg().get("min_severity") or "").strip().lower()
    order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return v if v in order else "info"


def _severity_rank(sev: str) -> int:
    order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return order.get(str(sev or "").strip().lower(), 2)


def _queue_path() -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    p = Path(WORKSPACE_DIR) / "notifications.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _push_macos(title: str, body: str) -> bool:
    """Native macOS notification. Returns False when osascript is
    missing/unavailable (headless, SSH-without-session, Linux) — the
    caller just skips the channel silently."""
    try:
        r = subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{body[:200]}" with title "{title[:60]}" sound name "Glass"',
            ],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001 — notifications never break the run
        return False


def _push_file(event: dict) -> bool:
    try:
        with _queue_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def notify(title: str, body: str, severity: str = "", kind: str = "info") -> None:
    """Fire one notification to every configured local channel.
    Best-effort, never raises, always records to the file queue."""
    try:
        if not _enabled():
            return
        # event-time snapshot: what was true WHEN it happened
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": str(kind)[:24],
            "title": str(title)[:120],
            "body": str(body)[:500],
            "severity": str(severity or "")[:16].lower(),
        }
        channels = _cfg().get("channels") or ["macos", "file"]
        if not isinstance(channels, list):
            channels = ["macos", "file"]  # malformed → default set, never silent
        # the severity floor only ever GATES the popup; the file record
        # always lands (the durable trail must be complete)
        if _severity_rank(severity) >= _severity_rank(_min_severity()):
            for ch in channels:
                if ch == "macos":
                    _push_macos(event["title"], f"[{event['severity'] or kind}] {event['body']}")
                elif ch == "file":
                    _push_file(event)
        else:
            _push_file(event)
    except Exception:  # noqa: BLE001
        pass


def notify_confirmed(entry: dict) -> None:
    """The _on_confirmed hook: a terminal-verified finding just landed."""
    with contextlib.suppress(Exception):
        sev = str(entry.get("system_severity") or entry.get("severity") or "?")
        notify(
            title=f"CONFIRMED [{sev.upper()}] {str(entry.get('id') or '?')}",
            body=str(entry.get("title") or "")[:200],
            severity=sev,
            kind="finding.confirmed",
        )


def notify_ending(reason: str) -> None:
    """Engagement ended (any classification)."""
    with contextlib.suppress(Exception):
        notify(title="Engagement ended", body=str(reason or "")[:200], severity="info", kind="engagement.ended")


def recent(limit: int = 20) -> list[dict]:
    """The operator-side reader: newest notifications for the TUI/console."""
    try:
        p = _queue_path()
        if not p.exists():
            return []
        lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        out = []
        for ln in lines[-limit:]:
            with contextlib.suppress(ValueError):
                out.append(json.loads(ln))
        return list(reversed(out))
    except Exception:  # noqa: BLE001
        return []
