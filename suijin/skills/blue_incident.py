"""Blue incident response skill."""
BLUE_INCIDENT_PROMPT = """
## BLUE SKILL: INCIDENT RESPONSE
When a critical threat is confirmed, follow the IR playbook.
## WORKFLOW
1. Declare incident — notify operator immediately
2. Collect forensic evidence — preserve all logs, requests, attacker profile
3. Isolate affected endpoints — rate-limit or temporarily disable if needed
4. Deploy hotfix if vulnerability confirmed
5. Build incident timeline for post-mortem
"""
