"""Kernel health — per-module last-boot status, the data behind the boot
report, doctor, and the Module Manager detail pane. Stdlib only.
"""

from __future__ import annotations

import time
from typing import Optional


class HealthTracker:
    """Records the latest boot outcome per module id."""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def record_boot(self, module_id: str, status: str, detail: str = "") -> None:
        """status: ok | skipped | quarantined | failed."""
        self._entries[module_id] = {
            "status": status,
            "detail": detail,
            "at": time.time(),
        }

    def get(self, module_id: str) -> Optional[dict]:
        return self._entries.get(module_id)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._entries.values():
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        return counts

    def problems(self) -> dict[str, dict]:
        """Everything not ok — what the boot report surfaces."""
        return {mid: e for mid, e in self._entries.items() if e["status"] != "ok"}
