"""Shift Manager — watcher allocation, rotation, health."""
class ShiftManager:
    def __init__(self, config: dict):
        self.config = config
        self.active_watchers = {}
        self.check_interval = config.get("soc",{}).get("shift_check_interval", 60)
    def allocate(self, endpoints: list) -> dict:
        return {ep.get("path","/"): 1 for ep in endpoints}
