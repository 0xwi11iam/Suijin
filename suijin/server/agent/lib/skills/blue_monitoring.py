"""Blue monitoring skill — endpoint watching workflow."""

BLUE_MONITORING_PROMPT = """
## BLUE SKILL: ENDPOINT MONITORING
Watch all endpoints for anomalous traffic. Score every request 1-10.
## WORKFLOW
1. Establish normal traffic baseline per endpoint (10 turns, no LLM cost)
2. Score incoming requests against baseline
3. Score 5-7: flag for Tier 2 validation
4. Score 8-10: instant response — block, deceive, or shadow-redirect
5. Escalate attack campaigns to SOC Lead
"""
