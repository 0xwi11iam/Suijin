"""
suijin/core/blue/session_manager.py — Blue team session state.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Optional


def _workspace_dir():
    from suijin.modules.platform.lib.workspace import WORKSPACE_DIR as _v

    return _v


STATE_DIR = None  # patchable seam; resolved lazily (boundary rule)


def _state_dir():
    v = globals().get("STATE_DIR")
    if v is not None:
        return v
    from suijin.modules.platform.lib.workspace import artifact_dir

    return artifact_dir("blue_state")


def _ensure_dir() -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)


_ensure_dir()  # module init: this module IS the state manager — creating
# its own dir on import is its documented purpose


class AttackerProfile:
    def __init__(self, attacker_id: str, first_seen_ip: str):
        self.attacker_id = attacker_id
        self.ips: set = {first_seen_ip}
        self.first_seen = datetime.now(timezone.utc)
        self.last_seen = self.first_seen
        self.request_count = 0
        self.endpoints_hit: set = set()
        self.payloads_seen: list = []
        self.tools_detected: set = set()
        self.skill_assessment = "unknown"
        self.threat_score = 1.0
        self.is_shadowed = False
        self.deception_active = False
        self.dossier: dict = {}

    def update(self, ip: str, endpoint: str, payload: str = ""):
        self.ips.add(ip)
        self.endpoints_hit.add(endpoint)
        self.last_seen = datetime.now(timezone.utc)
        self.request_count += 1
        if payload and len(self.payloads_seen) < 50:
            self.payloads_seen.append(payload[:200])

    def to_dict(self) -> dict:
        return {
            "id": self.attacker_id,
            "ips": list(self.ips),
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "requests": self.request_count,
            "endpoints": list(self.endpoints_hit),
            "skill": self.skill_assessment,
            "threat_score": self.threat_score,
            "is_shadowed": self.is_shadowed,
        }


class BlueSession:
    def __init__(self, target_codebase: str):
        self.session_id = hashlib.sha256(
            f"{target_codebase}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:12]
        self.target_codebase = target_codebase
        self.started_at = datetime.now(timezone.utc)
        self.endpoints_discovered = 0
        self.total_requests_processed = 0
        self.threats_blocked = 0  # BF0: flaggings (detected); enforcement in feed stats
        self.threats_deceived = 0
        self.hotfixes_deployed = 0
        self.active_watchers = 0
        self.total_cost_usd = 0.0
        self.attackers: dict[str, AttackerProfile] = {}

        # Subagent tracking
        self.subagents_deployed = 0
        self.subagent_analyses = 0  # Total AI analyses run by subagents
        self.subagent_anomalies = 0  # Anomalies flagged by subagents
        self.baseline_established = False
        self.baseline_request_count = 0

        self._lock = threading.Lock()

    def get_or_create_attacker(self, ip: str) -> AttackerProfile:
        with self._lock:
            for _aid, profile in self.attackers.items():
                if ip in profile.ips:
                    return profile
            aid = f"ATTK-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{len(self.attackers) + 1:04d}"
            profile = AttackerProfile(aid, ip)
            self.attackers[aid] = profile
            return profile

    def save(self):
        path = _state_dir() / f"session_{self.session_id}.json"
        path.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "target": self.target_codebase,
                    "started_at": self.started_at.isoformat(),
                    "stats": {
                        "requests": self.total_requests_processed,
                        "detected": self.threats_blocked,  # legacy field name; now = flaggings
                        "deceived": self.threats_deceived,
                        "hotfixes": self.hotfixes_deployed,
                        "watchers": self.active_watchers,
                        "cost": self.total_cost_usd,
                    },
                    "attackers": {k: v.to_dict() for k, v in self.attackers.items()},
                },
                indent=2,
                default=str,
            )
        )


_global_session: Optional[BlueSession] = None


def init_session(target_codebase: str) -> BlueSession:
    global _global_session
    _global_session = BlueSession(target_codebase)
    return _global_session


def get_session() -> Optional[BlueSession]:
    return _global_session
