"""Watcher fleet — per-endpoint zero-LLM sentinels (BF2).

Each watcher owns one endpoint: pattern/velocity/canary checks in
sub-second time, no LLM anywhere. Seeded from the Phase-1.5 endpoint
analysis (risk score, normal_patterns — its first real consumer). On a
critical hit the watcher AUTO-ENFORCES the fast path (tarpit/block now,
report simultaneously — the primary can escalate or undo); lesser hits
report and wait.

The watchers key on the Hill's typed events when present and fall back
to the pattern checks on raw traffic entries otherwise.
"""

from __future__ import annotations

import re
import threading
import time

# fast-path patterns: (name, weight) — weight >= 4 auto-enforces
_CRIT_PATTERNS = [
    ("sql_injection", 4),
    ("xss_attempt", 4),
    ("xxe_attempt", 5),
    ("command_injection", 4),
    ("deserialization", 5),
    ("path_traversal", 3),
    ("ssrf_attempt", 4),
    ("scanner_ua", 3),
    ("auth_bypass_header", 5),
]
_COMPILED = {
    name: re.compile(p, re.I)
    for name, p in [
        ("sql_injection", r"union\s+select|'\s*or\s+'1'\s*=\s*'1|pg_sleep|sleep\("),
        ("xss_attempt", r"<script|onerror\s*=|javascript:"),
        ("xxe_attempt", r"<!entity|<!doctype\s+\w+\s*\["),
        ("command_injection", r";\s*(id|whoami|cat\s+/etc)\b|\$\([^)]{2,}\)"),
        ("deserialization", r"pickle\.loads|O:\d+:\""),
        ("path_traversal", r"\.\./|/etc/passwd"),
        ("ssrf_attempt", r"169\.254\.169\.254|127\.0\.0\.1:\d{4}"),
        ("scanner_ua", r"\b(sqlmap|nikto|nmap|gobuster|masscan|hydra|dirbuster|feroxbuster|ffuf)\b"),
        ("auth_bypass_header", r"x-admin\s*:\s*true|x-role\s*:\s*admin"),
    ]
}

# typed events that auto-enforce on sight (severity critical in the lab)
_CRIT_EVENT_TYPES = {"canary_used", "canary_metadata", "metadata_access", "vault_access", "vault_decrypt"}

_auth_fails: dict[str, list] = {}  # ip -> [timestamps]
_lock = threading.Lock()


class EndpointWatcher:
    """One endpoint's sentinel. Zero LLM. Sub-second."""

    def __init__(self, endpoint: dict, risk_score: int = 1, normal_patterns: list | None = None):
        self.endpoint = endpoint
        self.path = str(endpoint.get("path", "/"))
        self.risk = int(risk_score or 1)
        self.normal_patterns = list(normal_patterns or [])
        self.events_seen = 0
        self.fast_path_fired = 0

    def check(self, entry: dict) -> list[dict]:
        """One traffic entry -> list of findings (with fast_path flags).

        The caller applies fast-path enforcement for any finding whose
        'fast_path' is True (or severity >= 4) — the watcher itself stays
        pure so tests can drive it without side effects."""
        findings: list[dict] = []
        path = str(entry.get("path", ""))
        # route to this watcher by prefix (static or var-consumed)
        if not self._owns(path):
            return findings
        self.events_seen += 1
        text = " ".join(str(entry.get(k, "")) for k in ("body", "query", "path", "user_agent", "headers"))
        total = 0
        for name, weight in _CRIT_PATTERNS:
            rx = _COMPILED[name]
            if rx.search(text):
                findings.append(
                    {
                        "watcher": self.path,
                        "signal": name,
                        "weight": weight,
                        "ip": entry.get("ip", "?"),
                        "fast_path": weight >= 4,
                    }
                )
                total += weight
        # auth velocity (this endpoint's own memory)
        if "login" in self.path or "auth" in self.path:
            ip = str(entry.get("ip", "?"))
            now = time.time()
            with _lock:
                fails = [t for t in _auth_fails.get(ip, []) if now - t < 60]
                fails.append(now)
                _auth_fails[ip] = fails
            if len(fails) >= 5:
                findings.append(
                    {
                        "watcher": self.path,
                        "signal": "auth_fail_velocity",
                        "weight": 4,
                        "ip": ip,
                        "fast_path": True,
                        "count": len(fails),
                    }
                )
        return findings

    def check_event(self, event: dict) -> list[dict]:
        """A typed lab event (hill_events.jsonl shape)."""
        etype = str(event.get("type", ""))
        if not self._owns(str(event.get("path", ""))) and etype not in _CRIT_EVENT_TYPES:
            return []
        if etype in _CRIT_EVENT_TYPES:
            self.events_seen += 1
            return [
                {
                    "watcher": self.path,
                    "signal": etype,
                    "weight": 5,
                    "ip": event.get("ip", "?"),
                    "fast_path": True,
                    "severity": event.get("severity", "critical"),
                }
            ]
        return []

    def _owns(self, path: str) -> bool:
        ep = self.path
        if "<" in ep:
            prefix = ep.split("<")[0].rstrip("/")
            if not prefix:
                return True  # root-level var route owns everything below
            if not path.startswith(prefix + "/") and path != prefix:
                return False
            rest = path[len(prefix) :].lstrip("/")
            consumed = rest.split("/", 1)[0]
            after = rest[len(consumed) :]
            expected = ep.split("<", 1)[1].split(">", 1)[-1]
            return after == expected
        return path == ep or path.rstrip("/") == ep.rstrip("/")


def spawn_from_analysis(endpoints: list, subagent_map: dict | None = None) -> list[EndpointWatcher]:
    """Fleet from the endpoint index — risk/normal_patterns pulled from the
    Phase-1.5 analysis when available (their first real consumer)."""
    sm = subagent_map or {}
    fleet = []
    for ep in endpoints:
        sa = sm.get(str(ep.get("path", "")))
        fleet.append(
            EndpointWatcher(
                ep,
                risk_score=int(getattr(sa, "risk_score", 1) or 1) if sa else 1,
                normal_patterns=list(getattr(sa, "normal_patterns", []) or []) if sa else [],
            )
        )
    return fleet


def apply_fast_path(finding: dict, tarpit_fn=None, block_fn=None) -> str:
    """The auto-enforce step the caller runs for fast_path findings.
    Defaults route to the enforcement plane / tarpit protocol."""
    ip = str(finding.get("ip", "?"))
    sig = finding.get("signal", "?")
    if finding.get("signal") in ("canary_metadata", "vault_access", "vault_decrypt") and block_fn:
        return f"{block_fn(ip, f'watcher: {sig}')}"
    if tarpit_fn is None:
        from suijin.modules.blueteam.lib.blue.defense.tarpit import engage

        engage(ip, delay=8.0)
    else:
        tarpit_fn(ip, finding.get("weight", 4))
    if block_fn is None:
        from suijin.modules.blueteam.lib.blue import enforcement

        enforcement.block_ip(ip, f"watcher fast-path: {sig}")
    else:
        block_fn(ip, f"watcher fast-path: {sig}")
    return f"FAST PATH: {sig} from {ip} — tarpit + block applied instantly"


def watcher_report(findings: list) -> str:
    """Findings -> the message the primary sees."""
    if not findings:
        return ""
    lines = []
    for f in findings[:10]:
        acted = " [auto-enforced]" if f.get("fast_path") else ""
        lines.append(f"WATCHER {f['watcher']}: {f['signal']} (w{f['weight']}) from {f.get('ip', '?')}{acted}")
    return "\n".join(lines)


# ── compat shim: the OLD spawn_watchers signature (blueteamer Phase 2) ──
# now returns the REAL fleet (zero-LLM sentinels) instead of decorative
# counter objects — the phase keeps its shape, the watchers gain teeth.


async def spawn_watchers(endpoints: list, config: dict = None) -> dict:
    """One real sentinel per endpoint (async: blueteamer Phase 2 awaits)."""
    fleet = spawn_from_analysis(endpoints)
    return {f"watcher_{w.path.replace('/', '_')}": w for w in fleet}
