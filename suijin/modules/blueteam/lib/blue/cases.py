"""CaseStore — the blue team's case records (BF4).

Every confirmed attack opens/attaches to a case. Cases have a lifecycle
(new → contained → monitoring → closed), reopen + escalate on new
activity by the same actor, and carry a full timeline (verdicts,
actions, watcher findings, canary trips). MTTD/MTTR timestamps are
captured from birth — BF6 reads them, not reconstructs them.

Storage: outputs/blue_state/cases/session_<slug>.jsonl (append-only
timeline) + catalog.json (index). Cross-session readable for BF5 hunt.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# lifecycle transitions (event-driven, no timers — reopen escalates)
LIFECYCLE = ("new", "contained", "monitoring", "closed")

# ATT&CK technique mapping for the 19 pattern classes (BF6 heatmap source)
ATTACK_MAP = {
    "sql_injection": "T1190",
    "xss_attempt": "T1059.007",
    "xxe_attempt": "T1190",
    "command_injection": "T1059",
    "deserialization": "T1203",
    "path_traversal": "T1083",
    "ssrf_attempt": "T1190",
    "scanner_ua": "T1595",
    "auth_bypass_header": "T1550",
    "brute_force": "T1110",
    "cred_stuffing": "T1110.004",
    "jwt_tamper": "T1550.001",
    "idor_access": "T1222",
    "canary_used": "T1560",
    "exfil_dns": "T1048.003",
    "beacon_periodic": "T1071",
    "rate_burst": "T1499",
    "waf_bypass": "T1190",
    "debug_probe": "T1592",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cases_dir(engagement: str = "") -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    slug = re.sub(r"[^\w.-]", "_", str(engagement or "default").strip())[:48] or "default"
    d = WORKSPACE_DIR / "outputs" / "blue_state" / "cases" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_index(cdir: Path) -> dict:
    p = cdir / "catalog.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"cases": {}}


def _save_index(cdir: Path, index: dict) -> None:
    (cdir / "catalog.json").write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")


class CaseStore:
    """Case-centric actor intelligence. Every confirmed attack opens or
    attaches to the actor's case; the timeline is the audit trail."""

    def __init__(self, engagement: str = ""):
        self._dir = _cases_dir(engagement)
        self._index = _load_index(self._dir)

    # ── open / attach ─────────────────────────────────────────────────

    def record_event(
        self,
        ip: str,
        attack_type: str,
        score: int,
        endpoint: str,
        *,
        source: str = "detector",
        detail: str = "",
    ) -> dict:
        """Record an attack event: open a case (or attach to the actor's
        open case), append to the timeline, auto-transition lifecycle.
        Returns the case."""
        cases = self._index.setdefault("cases", {})

        # find the actor's open case (reopen on same-actor activity)
        cid = None
        for k, v in sorted(cases.items()):
            if v.get("actor_ip") == ip and v.get("status") != "closed":
                cid = k
                break

        now = _now_iso()
        if cid is None:
            n = len(cases) + 1
            cid = f"CASE-{n:04d}"
            while cid in cases:
                n += 1
                cid = f"CASE-{n:04d}"
            cases[cid] = {
                "id": cid,
                "actor_ip": ip,
                "attack_type": attack_type,
                "mitre": ATTACK_MAP.get(attack_type, ""),
                "status": "new",
                "severity": score,
                "opened_at": now,
                "first_event_at": now,
                "detected_at": now,
                "contained_at": None,
                "closed_at": None,
                "timeline": [],
                "endpoints": set(),
                "reopen_count": 0,
            }

        case = cases[cid]
        # escalate severity if this event is worse
        if score > case.get("severity", 0):
            case["severity"] = score
        case.setdefault("endpoints", set())
        if isinstance(case["endpoints"], list):
            case["endpoints"] = set(case["endpoints"])
        case["endpoints"].add(endpoint)

        # append timeline event
        case.setdefault("timeline", []).append(
            {
                "ts": now,
                "kind": source,
                "type": attack_type,
                "score": score,
                "endpoint": endpoint,
                "detail": detail[:200],
            }
        )

        self._save(case, cid)
        return case

    def record_action(self, cid: str, action: str, detail: str = "") -> dict | None:
        """Record a response action (block/tarpit/deceive/canary) —
        auto-transitions the case toward contained."""
        case = self._index["cases"].get(cid)
        if case is None:
            return None
        now = _now_iso()
        case["timeline"].append({"ts": now, "kind": "action", "type": action, "detail": detail[:200]})
        # first defensive action → contained (MTTR stops here)
        if case["status"] == "new":
            case["status"] = "contained"
            case["contained_at"] = now
        self._save(case, cid)
        return case

    def record_watcher(self, cid: str, watcher: str, signal: str, weight: int) -> dict | None:
        """Watcher findings attach to the case."""
        case = self._index["cases"].get(cid)
        if case is None:
            return None
        case["timeline"].append(
            {"ts": _now_iso(), "kind": "watcher", "type": signal, "watcher": watcher, "weight": weight}
        )
        self._save(case, cid)
        return case

    # ── lifecycle ─────────────────────────────────────────────────────

    def transition(self, cid: str, status: str, resolution: str = "") -> dict | None:
        """Manual lifecycle transition: contained → monitoring → closed.
        Reopening a closed case for the same actor is automatic via
        record_event."""
        case = self._index["cases"].get(cid)
        if case is None or status not in LIFECYCLE:
            return None
        now = _now_iso()
        if status == "closed":
            case["closed_at"] = now
            case["resolution"] = resolution
        elif status == "monitoring" and not case.get("contained_at"):
            case["contained_at"] = now
        case["status"] = status
        case["timeline"].append({"ts": now, "kind": "transition", "type": status, "detail": resolution})
        self._save(case, cid)
        return case

    # ── queries ───────────────────────────────────────────────────────

    def find_open_for_actor(self, ip: str) -> dict | None:
        for k, v in sorted(self._index["cases"].items()):
            if v.get("actor_ip") == ip and v.get("status") != "closed":
                return v
        return None

    def list_cases(self, status: str = "") -> list[dict]:
        out = []
        for k, v in sorted(self._index["cases"].items()):
            if not status or v.get("status") == status:
                d = dict(v)
                d["endpoints"] = (
                    sorted(v.get("endpoints", [])) if isinstance(v.get("endpoints"), set) else v.get("endpoints", [])
                )
                out.append(d)
        return out

    def case_detail(self, cid: str) -> dict | None:
        case = self._index["cases"].get(cid.upper())
        if case is None:
            return None
        d = dict(case)
        d["endpoints"] = (
            sorted(case.get("endpoints", [])) if isinstance(case.get("endpoints"), set) else case.get("endpoints", [])
        )
        return d

    def get_stats(self) -> dict:
        cases = list(self._index["cases"].values())
        open_cases = [c for c in cases if c.get("status") != "closed"]
        closed = [c for c in cases if c.get("status") == "closed"]
        mttds = []
        mttrs = []
        for c in cases:
            if c.get("detected_at") and c.get("first_event_at"):
                mttds.append(c["detected_at"])
            if c.get("contained_at") and c.get("detected_at"):
                mttrs.append(c["contained_at"])
        return {
            "total": len(cases),
            "open": len(open_cases),
            "closed": len(closed),
            "by_status": {s: sum(1 for c in cases if c.get("status") == s) for s in LIFECYCLE},
            "by_type": {},  # computed on demand
            "actors": len({c.get("actor_ip") for c in cases}),
            "reopen_count": sum(c.get("reopen_count", 0) for c in cases),
        }

    def actor_summary(self, ip: str) -> dict:
        """Per-actor dossier data: all cases, attacks, timeline summary."""
        cases = [c for c in self._index["cases"].values() if c.get("actor_ip") == ip]
        all_events = []
        for c in cases:
            all_events.extend(c.get("timeline", []))
        attack_types = {}
        for c in cases:
            attack_types[c.get("attack_type", "?")] = attack_types.get(c.get("attack_type", "?"), 0) + 1
        return {
            "ip": ip,
            "total_cases": len(cases),
            "open_cases": sum(1 for c in cases if c.get("status") != "closed"),
            "max_severity": max((c.get("severity", 0) for c in cases), default=0),
            "attack_types": attack_types,
            "mitre_techniques": sorted({c.get("mitre") for c in cases if c.get("mitre")}),
            "endpoints": sorted(
                {
                    e
                    for c in cases
                    for e in (
                        c.get("endpoints") or set() if isinstance(c.get("endpoints"), set) else c.get("endpoints", [])
                    )
                }
            ),
            "event_count": len(all_events),
            "first_seen": min((c.get("first_event_at", "") for c in cases), default=""),
            "last_seen": max(
                (c.get("timeline", [{}])[-1].get("ts", "") for c in cases if c.get("timeline")), default=""
            ),
        }

    # ── persistence ────────────────────────────────────────────────────

    def _save(self, case: dict, cid: str) -> None:
        """Persist: catalog index + append-only JSONL timeline."""
        # convert set to list for JSON
        if isinstance(case.get("endpoints"), set):
            case["endpoints"] = sorted(case["endpoints"])
        self._index["cases"][cid] = case
        _save_index(self._dir, self._index)
        # append the last timeline entry to the JSONL
        if case.get("timeline"):
            entry = dict(case["timeline"][-1])
            entry["case_id"] = cid
            entry["actor_ip"] = case.get("actor_ip")
            with contextlib_suppress(), (self._dir / f"{cid}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str, separators=(",", ":")) + "\n")


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True
