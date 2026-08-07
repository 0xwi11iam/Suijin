"""Attacker profiler — psychological and technical profile."""
import time

class AttackerProfiler:
    def __init__(self):
        self.profiles = {}
    def update(self, attacker_id: str, request: dict, score: int):
        if attacker_id not in self.profiles:
            self.profiles[attacker_id] = {"id":attacker_id,"first_seen":time.time(),"total_requests":0,"max_score":0,"endpoints":set(),"payload_types":set()}
        p = self.profiles[attacker_id]
        p["total_requests"] += 1
        p["max_score"] = max(p["max_score"], score)
        p["endpoints"].add(request.get("path","/"))
    def assess_skill(self, attacker_id: str) -> str:
        p = self.profiles.get(attacker_id,{})
        if p.get("max_score",0) >= 9: return "advanced"
        if p.get("max_score",0) >= 6: return "intermediate"
        return "script_kiddie"
