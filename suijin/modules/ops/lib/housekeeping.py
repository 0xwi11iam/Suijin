"""Lab campaign runner + traffic watcher + timeline + workspace cleaner.

campaign: boot every lab, probe reachability + flags + endpoint hints,
          produce a capability-matrix report (agent regression baseline).
watch:   live tail of the blue traffic log, scored per line.
timeline: unified chronological view across audits/sessions/reports.
clean:   dry-run-first workspace cleaner — archive + remove stale files.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path


def _ws():
    """Platform workspace accessors, resolved lazily (module boundary rule)."""
    from suijin.modules.platform.lib import workspace

    return workspace


FLAG_RE = re.compile(r"FLAG\{[^}]{2,80}\}")


# ── Campaign runner ────────────────────────────────────────────────────


def probe_lab(base_url: str, session) -> dict:
    """Reachability + flag + endpoint probe of one running lab."""
    result = {"reachable": False, "flags": [], "hints": []}
    try:
        r = session.get(base_url + "/", timeout=8)
        result["reachable"] = True
        result["flags"] = sorted(set(FLAG_RE.findall(r.text)))
        # landing pages commonly enumerate their routes
        paths = sorted(set(re.findall(r'"(/(?:api|auth|admin|graphql)[^"\s]{0,40})"', r.text)))[:8]
        result["hints"] = paths
        if not result["flags"]:
            try:
                h = session.get(base_url + "/health", timeout=5)
                result["flags"] = sorted(set(FLAG_RE.findall(h.text)))
            except Exception:
                pass
    except Exception:
        pass
    return result


def run_campaign(lab_specs: list[dict], session=None, out_dir: Path | None = None) -> dict:
    """Probe each {name, port} lab; return the capability matrix dict.

    Probing is passive (GET / and /health only) — exploitation is the
    agent's job; this is a regression baseline of what's exposed.
    """
    import requests

    req = session or requests.Session()
    matrix: dict[str, dict] = {}
    for spec in lab_specs:
        url = f"http://127.0.0.1:{spec['port']}"
        t0 = time.monotonic()
        probe = probe_lab(url, req)
        probe["latency_ms"] = int((time.monotonic() - t0) * 1000)
        matrix[spec["name"]] = probe
    out = {
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "labs": matrix,
        "summary": {
            "total": len(matrix),
            "reachable": sum(1 for v in matrix.values() if v["reachable"]),
            "flags_exposed": sum(len(v["flags"]) for v in matrix.values()),
        },
    }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"lab_campaign_{time.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(out, indent=2))
        out["_saved"] = str(path)
    return out


def render_campaign(result: dict) -> str:
    lines = [
        f"Lab campaign — {result['ran_at']}",
        f"reachable: {result['summary']['reachable']}/{result['summary']['total']} · "
        f"flags exposed on landing pages: {result['summary']['flags_exposed']}",
        "",
        f"  {'lab':18} {'up':4} {'flags':6} {'ms':>6}  hints",
    ]
    for name, m in result["labs"].items():
        hints = ", ".join(m["hints"][:3]) or "—"
        lines.append(
            f"  {name:18} {'yes' if m['reachable'] else 'NO':4} {len(m['flags']):<6} {m['latency_ms']:>6}  {hints}"
        )
    return "\n".join(lines)


# ── Traffic watcher ────────────────────────────────────────────────────


def watch_lines(lines: list[str], enrich=None) -> list[str]:
    """Score jsonl traffic lines into colored-ready output lines.

    Pure function so the CLI loop and tests share semantics.
    """
    from suijin.modules.tools.lib.services import get as _service

    score_request = _service("traffic_scorer")

    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if enrich:
            entry = enrich(entry)
        # seed the profile with the entry's own IP so watch output reflects
        # attack signals, not first-seen noise
        entry_ip = str(entry.get("ip", ""))
        profile = {"methods": {"GET": 1, "POST": 1}, "ips": {entry_ip, "127.0.0.1"}, "avg_body_size": 1000}
        verdict = score_request(entry, profile)
        tier = "INVESTIGATED" if verdict["score"] >= 5 else "ANOMALOUS" if verdict["score"] >= 3 else "normal"
        sig = ",".join(verdict["signals"][:3]) or "-"
        out.append(
            f"{entry.get('timestamp', '')[11:19]} {tier:>12} "
            f"{verdict['score']:>2} {entry.get('method', '?'):5} "
            f"{entry.get('path', '/')[:40]:40} {sig}"
        )
    return out


def tail_file(path: Path, poll: float = 0.5):
    """Yield appended lines forever (Ctrl+C to stop)."""
    path = Path(path)
    pos = 0
    while True:
        if path.exists():
            size = path.stat().st_size
            if size > pos:
                with open(path, errors="ignore") as f:
                    f.seek(pos)
                    data = f.read()
                    pos = f.tell()
                yield from data.splitlines()
            elif size < pos:  # truncated — restart
                pos = 0
        time.sleep(poll)


# ── Unified timeline ───────────────────────────────────────────────────


def build_timeline(workspace: Path | None = None, limit: int = 60) -> list[dict]:
    """Merge audit/session/report timestamps into one ascending timeline."""
    ws = Path(workspace) if workspace else _ws().WORKSPACE_DIR
    events: list[dict] = []

    audit_dir = ws / "audit_trails"
    if audit_dir.is_dir():
        for f in audit_dir.glob("*.json"):
            try:
                t = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            eng = t.get("engagement", f.stem)
            if t.get("started"):
                events.append({"ts": _normalize_ts(t["started"]), "kind": "engagement start", "detail": eng})
            if t.get("ended"):
                events.append(
                    {
                        "ts": _normalize_ts(t["ended"]),
                        "kind": "engagement end",
                        "detail": f"{eng} — {len(t.get('findings', []))} findings",
                    }
                )
    sessions = ws / "sessions"
    if sessions.is_dir():
        for f in sessions.glob("*.json"):
            try:
                s = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            saved = str(s.get("saved_at", ""))
            if saved:
                events.append(
                    {"ts": _normalize_ts(saved), "kind": "session saved", "detail": str(s.get("objective", ""))[:60]}
                )
    reports = ws / "reports"
    if reports.is_dir():
        for f in reports.rglob("*"):
            if f.is_file() and f.suffix in (".md", ".html"):
                events.append(
                    {
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                        "kind": "report",
                        "detail": str(f.relative_to(ws)),
                    }
                )
    events.sort(key=lambda e: e["ts"])
    return events[-limit:]


def _normalize_ts(raw: str) -> str:
    """'2026-08-17T10:00:00' / '20260817_100000' -> '2026-08-17 10:00:00'."""
    s = str(raw).replace("T", " ")
    if len(s) == 15 and s[8] == "_":  # saved_at compact form
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]}:{s[13:15]}"
    return s[:19]


# ── Workspace cleaner ──────────────────────────────────────────────────

_CLEAN_TARGETS = ("outputs", "notifications.log")


def find_stale(workspace: Path | None = None, age_days: int = 30, now: float | None = None) -> list[Path]:
    """Files in churn dirs older than age_days."""
    ws = Path(workspace) if workspace else _ws().WORKSPACE_DIR
    cutoff = (now or time.time()) - age_days * 86400
    stale: list[Path] = []
    for folder in _CLEAN_TARGETS[:-1]:
        d = ws / folder
        if d.is_dir():
            stale.extend(f for f in d.rglob("*") if f.is_file() and f.stat().st_mtime < cutoff)
    log = ws / _CLEAN_TARGETS[-1]
    if log.exists() and log.stat().st_mtime < cutoff:
        stale.append(log)
    return stale


def clean_workspace(apply: bool = False, age_days: int = 30, workspace: Path | None = None) -> str:
    """Dry-run by default; with apply=True archives then deletes stale files."""
    import zipfile

    ws = Path(workspace) if workspace else _ws().WORKSPACE_DIR
    stale = find_stale(ws, age_days)
    total_kb = sum(f.stat().st_size for f in stale) / 1024
    noun = f"{len(stale)} file(s), {total_kb:.0f} KB"
    if not stale:
        return f"Nothing stale (>{age_days}d) in outputs/sandbox — workspace is tidy."
    if not apply:
        lines = [f"[dry-run] would archive + delete {noun} (> {age_days} days old):"]
        lines += [f"  {f.relative_to(ws)}" for f in stale[:20]]
        lines.append("\nRun with --apply to archive them into suijin_agent/exports/ then delete.")
        return "\n".join(lines)
    export_dir = ws / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    archive = export_dir / f"cleaned_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for f in stale:
            zf.write(f, str(f.relative_to(ws)))
    for f in stale:
        f.unlink()
    return f"archived {noun} -> {archive.relative_to(ws)} and deleted originals"
