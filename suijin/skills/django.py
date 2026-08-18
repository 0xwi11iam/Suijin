"""
Django Attack Skill Prompt.
"""
DJANGO_SKILL_PROMPT = """
## ATTACK SKILL: DJANGO ATTACKS

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Django signatures: `csrftoken` cookie, `X-Django-Debug` header, `/admin/` login, `django_language` cookie.

#### STEP 2: DEBUG MODE EXPLOITATION
If `DEBUG=True` (500 error page with traceback): SQLite DB path in settings, SECRET_KEY in traceback, all installed apps listed.

#### STEP 3: SECRET_KEY ABUSE
If SECRET_KEY is leaked:
```python
from django.core.signing import TimestampSigner
signer = TimestampSigner(key='LEAKED_SECRET_KEY')
# Forge signed cookies, reset password tokens, session data
```

#### STEP 4: SQLITE DB EXPOSURE
Check: `/db.sqlite3`, `/data/db.sqlite3`, `/database/db.sqlite3`, `/.sqlite3`

#### ANTI-PATTERNS: Don't stop at 500 page — check /static/ for debug output. Test SECRET_KEY in ALL Django signing contexts.
"""
