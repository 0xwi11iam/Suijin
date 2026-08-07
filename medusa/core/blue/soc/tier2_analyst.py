"""Tier 2 Analyst — validates threats, correlates across endpoints."""
async def activate_tier2(config: dict, endpoint_group: str) -> dict:
    return {"role": "tier2", "endpoint_group": endpoint_group, "status": "active"}
