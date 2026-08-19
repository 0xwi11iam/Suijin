"""Blue-team ops (E33-E40).

E33 attack replay: red tool calls -> synthetic traffic entries scored by
     the detector (train/eval without a live lab).
E34 deception effectiveness: per-battle metrics of tarpit delay and
     honeypot hits.
E35 SOC playbooks: detections trigger registered response actions.
E36 FP feedback loop: analyst marks false positives -> threshold and
     allowlist learn.
E37 allowlist manager: known-benign signatures the detector skips.
E38 incident timeline: detections + responses as a chronological view.
E39 log adapters: nginx/Apache combined logs -> traffic entries.
E40 canary tripwires: canary credential generation + reuse watch.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

# ── E33: attack replay ─────────────────────────────────────────────────


def red_trace_to_traffic(trace: list) -> list:
    """Convert red execution-trace steps into detector-shaped entries."""
    out = []
    for step in trace or []:
        args = step.get("tool_args") or {}
        path = str(args.get("url", args.get("path", "/")))
        if not path.startswith("/"):
            try:
                from urllib.parse import urlsplit

                path = urlsplit(path).path or "/"
            except Exception:  # noqa: BLE001
                path = "/"
        out.append(
            {
                "method": str(args.get("method", "GET")).upper(),
                "path": path,
                "ip": "10.99.0.1",  # the red agent's synthetic source
                "headers": {},
                "body": str(args.get("body", args.get("payload", "")))[:2000],
                "_from_red_tool": step.get("tool_name", "?"),
            }
        )
    return out


def replay_red_through_detector(trace: list, threshold: int = 5) -> dict:
    """Score a red trace as if the requests had hit the blue proxy."""
    entries = red_trace_to_traffic(trace)
    from suijin.modules.blueteam.lib.blue.traffic.anomaly_detector import detect_anomalies

    tp = fn = 0
    misses = []
    for e in entries:
        signals = detect_anomalies(e, {"methods": {e["method"]: 1}, "ips": set(), "avg_body_size": 100})
        score = sum(s[1] for s in signals)
        if score >= threshold:
            tp += 1
        else:
            fn += 1
            misses.append(f"{e['_from_red_tool']}: {e['path'][:40]}")
    recall = tp / max(tp + fn, 1)
    return {
        "entries": len(entries),
        "caught": tp,
        "missed": fn,
        "recall": round(recall, 3),
        "miss_examples": misses[:6],
    }


# ── E34: deception effectiveness ───────────────────────────────────────


def deception_effectiveness(battle_events: list) -> str:
    """Metrics from battle-state events: did tarpits waste time, did
    honeypots absorb requests?"""
    tarpit_hits = [e for e in battle_events or [] if str(e.get("event", "")).startswith("tarpit")]
    honey_hits = [e for e in battle_events or [] if str(e.get("event", "")).startswith("honeypot")]
    wasted = sum(int(e.get("wasted_ms", 0)) for e in tarpit_hits)
    lines = [f"deception effectiveness: {len(tarpit_hits)} tarpit hit(s), {len(honey_hits)} honeypot hit(s)"]
    if tarpit_hits:
        lines.append(f"  attacker time wasted in tarpits: {wasted / 1000:.1f}s total")
    if honey_hits:
        paths = {str(e.get("path", "?")) for e in honey_hits}
        lines.append(f"  honeypot paths probed: {', '.join(sorted(paths)[:5])}")
    if not tarpit_hits and not honey_hits:
        lines.append("  no deception was triggered this battle")
    return "\n".join(lines)


# ── E35: SOC playbooks ─────────────────────────────────────────────────

_PLAYBOOKS: dict[str, list[str]] = {}


def register_playbook(detection_type: str, actions: list[str]) -> None:
    """Register response actions for a detection type (module code)."""
    _PLAYBOOKS[detection_type] = actions


def run_playbook(detection: dict) -> list[str]:
    """Fire the playbook for a detection; returns executed action names."""
    dtype = str(detection.get("type", "")).lower()
    actions = _PLAYBOOKS.get(dtype, [])
    if not actions:
        return []
    return list(actions)


# ── E36+E37: FP loop + allowlist ───────────────────────────────────────


def _allowlist_path() -> Path:
    from suijin.modules.platform.lib.workspace import artifact_dir

    d = artifact_dir("blue_state")
    d.mkdir(parents=True, exist_ok=True)
    return d / "allowlist.json"


def _load_allowlist() -> list:
    p = _allowlist_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def allowlist_add(pattern: str, reason: str = "") -> str:
    p = _allowlist_path()
    entries = _load_allowlist()
    entry = {"pattern": pattern, "reason": reason[:120], "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if any(e.get("pattern") == pattern for e in entries):
        return f"already allowlisted: {pattern}"
    entries.append(entry)
    p.write_text(json.dumps(entries, indent=2))
    return f"allowlisted {pattern!r}" + (f" ({reason[:60]})" if reason else "")


def allowlist_check(entry: dict) -> bool:
    """True when a traffic entry matches a known-benign signature."""
    sigs = [e["pattern"].lower() for e in _load_allowlist()]
    probe = f"{entry.get('path', '')} {entry.get('ip', '')}".lower()
    return any(s in probe for s in sigs if s)


def mark_false_positive(detection: dict) -> str:
    """E36: analyst FP feedback -> auto-allowlist the signature."""
    sig = str(detection.get("path", detection.get("pattern", "")))
    if not sig:
        return "nothing to allowlist from this detection"
    return allowlist_add(sig, reason=f"analyst-marked FP: {detection.get('type', '?')}")


# ── E38: incident timeline ─────────────────────────────────────────────


def incident_timeline(events: list) -> str:
    """Chronological detection+response view for post-incident review."""
    rows = []
    for e in sorted(events or [], key=lambda x: str(x.get("ts", ""))):
        kind = e.get("kind", "event")
        rows.append(f"[{str(e.get('ts', '?'))[:19]}] {kind:10} {str(e.get('detail', e.get('type', '')))[:70]}")
    return "\n".join(rows) or "no events"


# ── E39: log adapters ──────────────────────────────────────────────────

_NGINX = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>\w+) (?P<path>\S+)[^"]*" (?P<status>\d+) (?P<size>\d+)'
)


def parse_nginx_log(text: str) -> list:
    """nginx/Apache combined-format lines -> detector entries."""
    out = []
    for line in (text or "").splitlines():
        m = _NGINX.match(line)
        if m:
            path = m.group("path").split("?")[0]
            out.append(
                {
                    "ip": m.group("ip"),
                    "method": m.group("method"),
                    "path": path,
                    "headers": {},
                    "body": "",
                    "status": int(m.group("status")),
                }
            )
    return out


# ── E40: canary tripwires ──────────────────────────────────────────────


def generate_canaries(count: int = 3, prefix: str = "svc") -> list:
    """Canary credentials (fake API keys/users) planted in configs; any
    authentication attempt with them is an intrusion signal."""
    out = []
    for i in range(max(1, min(int(count or 3), 10))):
        kind = "key" if i % 2 == 0 else "user"
        value = f"{prefix}_canary_{i}_{secrets.token_hex(8)}"
        out.append({"kind": kind, "value": value, "note": f"canary {kind} #{i}"})
    return out


def watch_canary(event: dict, canaries: list) -> str | None:
    """Returns a tripwire message when an event references a canary."""
    blob = json.dumps(event, default=str).lower()
    for c in canaries or []:
        if str(c["value"]).lower() in blob:
            return f"CANARY TRIPWIRE: {c['note']} referenced — credential reuse by unauthorized party"
    return None
