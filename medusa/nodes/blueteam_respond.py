"""Blue team respond node — threat response actions."""
async def blueteam_respond_node(state: dict, generate_fn) -> dict:
    return {"phase": "respond", "actions_taken": [], "state": state}
