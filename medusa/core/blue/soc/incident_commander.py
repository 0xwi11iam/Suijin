"""Incident Commander — major incident response coordination."""
import time

class IncidentCommander:
    def __init__(self):
        self.active_incidents: list = []
        self.resolved: list = []

    def declare_incident(self, ip: str, attack_type: str, score: int, affected_endpoints: list) -> dict:
        incident = {
            "id": f"INC-{len(self.active_incidents)+1:04d}",
            "ip": ip, "type": attack_type, "score": score,
            "endpoints": affected_endpoints,
            "declared_at": time.time(), "status": "active",
        }
        self.active_incidents.append(incident)
        return incident

    def resolve(self, incident_id: str, resolution: str):
        for inc in self.active_incidents:
            if inc["id"] == incident_id:
                inc["status"] = "resolved"
                inc["resolution"] = resolution
                inc["resolved_at"] = time.time()
                self.resolved.append(inc)
                self.active_incidents.remove(inc)
                return inc
        return None

    def get_active(self) -> list:
        return self.active_incidents

    def get_stats(self) -> dict:
        return {"active": len(self.active_incidents), "resolved": len(self.resolved)}


def create_incident_commander() -> IncidentCommander:
    return IncidentCommander()
