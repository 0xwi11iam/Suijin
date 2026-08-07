"""Blue patching skill — automated hotfix workflow."""
BLUE_PATCHING_PROMPT = """
## BLUE SKILL: AUTOMATED PATCHING
When a vulnerability is confirmed, generate and deploy a fix.
## WORKFLOW
1. Confirm vulnerability with evidence (diff response, attack replay)
2. Generate fix: parameterized query, input sanitization, output encoding
3. Run existing tests — verify no regression
4. Present patch to operator for approval
5. Deploy to production
6. Optionally keep vulnerable endpoint as trap (silent patch mode)
"""
