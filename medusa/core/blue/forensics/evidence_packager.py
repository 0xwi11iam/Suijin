"""Evidence packager — bundle evidence for operator or IR."""
import json, time
from pathlib import Path

def package_evidence(incident_id: str, timeline: list, attacker_profile: dict, logs: list) -> str:
    pkg = {"incident_id": incident_id, "packaged_at": time.time(), "timeline": timeline,
           "attacker": attacker_profile, "log_samples": logs[:5]}
    path = Path(__file__).resolve().parent.parent.parent.parent / "medusa_agent" / "evidence" / f"{incident_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pkg, indent=2, default=str))
    return str(path)
