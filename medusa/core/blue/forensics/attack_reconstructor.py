"""Attack reconstructor — rebuild attacker's full session."""
from __future__ import annotations
def reconstruct_timeline(ip: str, requests: list) -> list:
    timeline = []
    for req in requests:
        if req.get("ip") == ip:
            timeline.append({"time": req.get("timestamp","?"), "method": req.get("method","?"),
                "path": req.get("path","?"), "status": req.get("status","?"), "payload": str(req.get("body",""))[:100]})
    return sorted(timeline, key=lambda x: x["time"])
