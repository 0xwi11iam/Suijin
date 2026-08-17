"""Result collector — aggregate findings, deduplicate."""
from __future__ import annotations

import hashlib
import time


class ResultCollector:
    def __init__(self):
        self.findings = []
        self._seen = set()
    def add(self, finding: dict):
        key = hashlib.sha256(str(finding).encode()).hexdigest()[:16]
        if key not in self._seen:
            self._seen.add(key)
            finding["id"] = key
            finding["timestamp"] = time.time()
            self.findings.append(finding)
    def get_recent(self, count: int = 50) -> list:
        return self.findings[-count:]
