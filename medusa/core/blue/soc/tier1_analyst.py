"""Tier 1 Analyst — first responder, initial triage."""
async def activate_tier1(config: dict, endpoint: dict) -> dict:
    return {"role": "tier1", "endpoint": endpoint.get("path","/"), "status": "active"}
