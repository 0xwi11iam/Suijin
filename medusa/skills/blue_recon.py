"""Blue team recon skill — map own attack surface."""
BLUE_RECON_PROMPT = """
## BLUE SKILL: DEFENSIVE RECONNAISSANCE
Map your own attack surface. Run the codebase scanner, identify every endpoint, assess risk per endpoint.
## WORKFLOW
1. Scan codebase for routes and handlers
2. Identify auth status per endpoint
3. Score risk: no-auth endpoints > raw SQL endpoints > admin paths > sensitive operations
4. Report findings to operator with prioritized watch list
"""
