"""Evidence packager — bundle evidence for operator or IR."""

from __future__ import annotations

import json
import time

from medusa.tools.workspace import WORKSPACE_DIR


def package_evidence(incident_id: str, timeline: list, attacker_profile: dict, logs: list) -> str:
    pkg = {
        "incident_id": incident_id,
        "packaged_at": time.time(),
        "timeline": timeline,
        "attacker": attacker_profile,
        "log_samples": logs[:5],
    }
    path = WORKSPACE_DIR / "evidence" / f"{incident_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pkg, indent=2, default=str))
    return str(path)
