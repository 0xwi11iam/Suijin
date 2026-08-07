"""Attack chain tracker — correlate attacker requests."""
class AttackChainTracker:
    def __init__(self):
        self.chains = {}
    def add(self, attacker_id: str, request: dict):
        if attacker_id not in self.chains:
            self.chains[attacker_id] = []
        self.chains[attacker_id].append(request)
    def get_chain(self, attacker_id: str) -> list:
        return self.chains.get(attacker_id, [])
