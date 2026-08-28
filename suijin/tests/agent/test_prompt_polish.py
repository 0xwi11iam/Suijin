"""Professional prompt contract — the predator-era text triggered model
refusals on live bug-bounty engagements; the prompt stays professional."""

from suijin.modules.agent.lib.prompts.base import base_prompt


def test_no_jailbreak_smell_language():
    low = base_prompt.lower()
    for bad in ("predator", "no restrictions", "no limits", "pure freedom", "i dominate"):
        assert bad not in low, bad


def test_program_compliance_block_present():
    assert "PROGRAM COMPLIANCE" in base_prompt
    assert "prohibits automated" in base_prompt.lower()
    # comply-and-continue, never bulldoze, never stall
    assert "comply and continue" in base_prompt.lower()
