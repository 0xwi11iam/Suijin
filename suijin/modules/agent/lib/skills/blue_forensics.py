"""Blue forensics skill — log analysis."""

BLUE_FORENSICS_PROMPT = """
## BLUE SKILL: FORENSIC ANALYSIS
Investigate attacker activity across logs and endpoints.
## WORKFLOW
1. Search logs by IP, pattern, time range
2. Correlate across endpoints — build full attacker session timeline
3. Identify tool stack (sqlmap, Burp, custom scripts)
4. Assess attacker skill level
5. Build dossier for future reference
"""
