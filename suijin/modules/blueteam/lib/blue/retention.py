"""Traffic retention + IOC extraction + retro-sweep hunt (BF5).

Retention: traffic shards in outputs/blue_traffic/<session>/shard-N.jsonl
(rotate at 10MB, cap at 5 shards, NEVER truncate at session start).
Response status added to the logged shape.

IOC extraction: from confirmed attacks (pattern name + payload signature
+ IP), canary hits (token + IP + path), stored as KG intelligence nodes.

Hunt: `suijin blue hunt` loads all retained shards, matches against the
IOC store (KG intelligence + canary tokens), opens hunt cases referencing
the original. Cross-session: session 2's hunt finds session 1's IOC.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

SHARD_MAX_BYTES = 10_000_000  # rotate at 10MB
SHARD_CAP = 5  # keep at most 5 shards per session


def _traffic_dir(session: str = "") -> Path:
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

    slug = re.sub(r"[^\w.-]", "_", str(session or "default").strip())[:48] or "default"
    d = WORKSPACE_DIR / "outputs" / "blue_traffic" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


class TrafficRetention:
    """Append-only traffic shards with rotate + cap."""

    def __init__(self, session: str = ""):
        self._dir = _traffic_dir(session)
        self._shard_idx = self._current_shard_index()

    def _current_shard_index(self) -> int:
        shards = sorted(self._dir.glob("shard-*.jsonl"))
        return len(shards)

    def _shard_path(self, idx: int) -> Path:
        return self._dir / f"shard-{idx:04d}.jsonl"

    def append(self, entry: dict) -> None:
        """Append a traffic entry to the current shard, rotating if full."""
        # find the current shard (or create the first)
        path = self._shard_path(self._shard_idx)
        if path.exists() and path.stat().st_size >= SHARD_MAX_BYTES:
            self._shard_idx += 1
            path = self._shard_path(self._shard_idx)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str, separators=(",", ":")) + "\n")
        # enforce cap (delete oldest beyond SHARD_CAP)
        shards = sorted(self._dir.glob("shard-*.jsonl"))
        while len(shards) > SHARD_CAP:
            shards[0].unlink()
            shards = shards[1:]

    def entries(self, session: str = "") -> list[dict]:
        """Read all entries from all shards (current session or another)."""
        d = self._dir if not session else _traffic_dir(session)
        out = []
        for shard in sorted(d.glob("shard-*.jsonl")):
            with contextlib_suppress():
                for line in shard.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return out

    @staticmethod
    def all_sessions() -> list[str]:
        """Every session that has retained traffic."""
        from suijin.modules.platform.lib.workspace import WORKSPACE_DIR

        root = WORKSPACE_DIR / "outputs" / "blue_traffic"
        if not root.is_dir():
            return []
        return sorted(d.name for d in root.iterdir() if d.is_dir() and any(d.glob("shard-*.jsonl")))


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return True


# ── IOC extraction ──────────────────────────────────────────────────────


def extract_iocs(case: dict) -> list[dict]:
    """Extract IOCs from a case: pattern names, payload signatures,
    endpoint patterns. Each IOC is a dict with type, value, source."""
    iocs = []
    actor = case.get("actor_ip", "")
    for event in case.get("timeline", []):
        if event.get("kind") == "detector":
            iocs.append(
                {
                    "type": "attack_pattern",
                    "value": event.get("type", ""),
                    "actor": actor,
                    "endpoint": event.get("endpoint", ""),
                    "ts": event.get("ts", ""),
                    "source_case": case.get("id", ""),
                }
            )
        elif event.get("kind") == "action" and event.get("type") == "DECEIVE":
            iocs.append(
                {
                    "type": "deception_trigger",
                    "value": event.get("detail", "")[:100],
                    "actor": actor,
                    "ts": event.get("ts", ""),
                    "source_case": case.get("id", ""),
                }
            )
    # dedup by (type, value)
    seen = set()
    out = []
    for ioc in iocs:
        key = (ioc["type"], ioc["value"])
        if key not in seen:
            seen.add(key)
            out.append(ioc)
    return out


def store_iocs(iocs: list[dict], kg) -> int:
    """Store IOCs as KG intelligence nodes (the add_intelligence API)."""
    stored = 0
    for ioc in iocs:
        try:
            kg.add_intelligence(
                f"ioc:{ioc['type']}:{ioc['value']}", f"actor={ioc['actor']} case={ioc.get('source_case', '?')}"
            )
            stored += 1
        except Exception:
            pass
    return stored


def load_iocs(kg) -> list[dict]:
    """Load all stored IOCs from KG intelligence nodes."""
    out = []
    try:
        intel = kg.get_intelligence() if hasattr(kg, "get_intelligence") else []
        for node_id, data in (intel or {}).items():
            if node_id.startswith("ioc:"):
                _, itype, value = node_id.split(":", 2)
                detail = str(data)[:200]
                # parse actor from detail (store_iocs embeds it as actor=X)
                import re as _re

                actor_m = _re.search(r"actor[=:]\s*([\d.]+)", detail)
                actor = actor_m.group(1) if actor_m else ""
                out.append({"type": itype, "value": value, "detail": detail, "actor": actor})
    except Exception:
        pass
    return out


# ── Hunt (retro-sweep) ─────────────────────────────────────────────────


def hunt(kg, enforcement_snapshot: dict | None = None, sessions: list[str] | None = None) -> dict:
    """Retro-sweep retained traffic against the IOC store. Returns
    findings that open hunt cases (cross-session lineage)."""
    iocs = load_iocs(kg)
    if not iocs and not enforcement_snapshot:
        return {"scanned": 0, "ioc_count": 0, "findings": [], "sessions_scanned": []}

    # add canary tokens as IOCs
    if enforcement_snapshot:
        for token, info in (enforcement_snapshot.get("canaries") or {}).items():
            iocs.append({"type": "canary_token", "value": token, "detail": str(info)[:200]})

    # determine sessions to scan
    scan_sessions = sessions or TrafficRetention.all_sessions()
    findings = []
    scanned = 0

    for session_name in scan_sessions:
        retention = TrafficRetention(session_name)
        entries = retention.entries()
        scanned += len(entries)
        for entry in entries:
            for ioc in iocs:
                matched = False
                if ioc["type"] == "attack_pattern":
                    # match on actor IP (retro-sweep known-bad actors)
                    actor = ioc.get("actor", "")
                    if actor and entry.get("ip", "") == actor:
                        matched = True
                    else:
                        # or on attack-type signature in path/query
                        path = str(entry.get("path", "")).lower()
                        query = str(entry.get("query", {})).lower()
                        sig = ioc["value"].lower().replace("_", " ")
                        if sig in path or sig in query:
                            matched = True
                elif ioc["type"] == "canary_token":
                    # match on body containing the token
                    if ioc["value"] in str(entry.get("body", "")):
                        matched = True

                if matched:
                    findings.append(
                        {
                            "session": session_name,
                            "ioc_type": ioc["type"],
                            "ioc_value": ioc["value"],
                            "entry_ip": entry.get("ip", "?"),
                            "entry_path": entry.get("path", "?")[:80],
                            "entry_ts": entry.get("timestamp", "?"),
                            "detail": ioc.get("detail", ""),
                        }
                    )

    return {
        "scanned": scanned,
        "ioc_count": len(iocs),
        "findings": findings[:50],  # cap for rendering
        "sessions_scanned": scan_sessions,
    }


def render_hunt(result: dict) -> str:
    """Markdown hunt report."""
    lines = [
        f"# Hunt Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"- entries scanned: {result['scanned']:,}",
        f"- IOCs loaded: {result['ioc_count']}",
        f"- sessions: {', '.join(result['sessions_scanned']) or '(none)'}",
        f"- findings: {len(result['findings'])}",
        "",
    ]
    for f in result["findings"][:20]:
        lines.append(f"- [{f['ioc_type']}] {f['ioc_value']} → {f['entry_ip']} hit {f['entry_path']} ({f['session']})")
    if not result["findings"]:
        lines.append("(no matches)")
    return "\n".join(lines)
