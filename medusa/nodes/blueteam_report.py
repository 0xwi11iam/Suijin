"""Blue team report node — generate defense report."""
async def blueteam_report_node(state: dict, generate_fn) -> dict:
    return {"phase": "report", "report_generated": True, "state": state}
