"""Traffic normalizer — learn normal patterns, zero LLM cost."""
import json, math, hashlib
from collections import defaultdict
from pathlib import Path

class TrafficNormalizer:
    def __init__(self):
        self.profiles = {}
        self.training_complete = False
        self.samples_seen = 0

    def train(self, requests: list, training_turns: int = 10):
        for req in requests:
            ep = req.get("path", "/")
            if ep not in self.profiles:
                self.profiles[ep] = {"methods": defaultdict(int), "statuses": defaultdict(int),
                    "param_names": defaultdict(int), "body_sizes": [], "user_agents": defaultdict(int),
                    "ips": set(), "request_count": 0}
            p = self.profiles[ep]
            p["methods"][req.get("method","GET")] += 1
            p["statuses"][str(req.get("status",200))] += 1
            p["ips"].add(req.get("ip",""))
            p["request_count"] += 1
            self.samples_seen += 1
        if self.samples_seen >= training_turns * 2:
            self.training_complete = True

    def is_normal(self, request: dict) -> bool:
        if not self.training_complete: return True
        ep = request.get("path","/")
        p = self.profiles.get(ep)
        if not p: return False
        method = request.get("method","GET")
        if p["methods"].get(method, 0) == 0: return False
        return True

    def get_profile(self, endpoint: str) -> dict:
        return self.profiles.get(endpoint, {"request_count": 0, "ips": []})
