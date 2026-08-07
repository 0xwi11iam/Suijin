"""Watcher spawner — create, assign, and manage endpoint watchers."""
import asyncio, uuid

async def spawn_watchers(endpoints: list, config: dict) -> dict:
    tasks = {}
    max_per = config.get("watchers",{}).get("max_per_endpoint", 3)
    for ep in endpoints[:50]:
        for i in range(min(1, max_per)):
            wid = f"watcher_{ep.get('path','/').replace('/','_')}_{i}"
            tasks[wid] = {"endpoint": ep, "status": "active", "spawned_at": "now"}
    return tasks
