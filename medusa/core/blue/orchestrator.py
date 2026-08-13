"""Blue team orchestrator — central conductor for the entire SOC."""
from __future__ import annotations
import asyncio, json, logging, time
from datetime import datetime, timezone
from pathlib import Path
from medusa.core.blue.config import load_blue_config
from medusa.core.blue.session_manager import get_session, init_session

logger = logging.getLogger(__name__)

class BlueOrchestrator:
    def __init__(self, target_path: str):
        self.target_path = Path(target_path).resolve()
        self.config = load_blue_config()
        self.session = init_session(str(self.target_path))
        self.endpoints = []
        self.watcher_tasks = {}
        self.soc_agents = {}
        self.threat_queue = asyncio.Queue()
        self.deception_queue = asyncio.Queue()
        self.running = False

    async def start(self):
        self.running = True
        from medusa.core.blue.codebase.scanner import scan_codebase
        self.endpoints = scan_codebase(str(self.target_path))
        self.session.endpoints_discovered = len(self.endpoints)
        from medusa.core.blue.watchers.spawner import spawn_watchers
        self.watcher_tasks = await spawn_watchers(self.endpoints, self.config)
        self.session.active_watchers = len(self.watcher_tasks)
        from medusa.core.blue.soc.soc_lead import activate_soc_lead
        self.soc_agents["lead"] = await activate_soc_lead(self.config, self.threat_queue)
        while self.running:
            await asyncio.sleep(1)

    async def stop(self):
        self.running = False
        self.session.save()

def run(target_path: str):
    orch = BlueOrchestrator(target_path)
    asyncio.run(orch.start())
