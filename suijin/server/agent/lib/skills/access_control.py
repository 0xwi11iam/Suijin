"""
Access Control / IDOR Attack Skill Prompt.
"""

ACCESS_CONTROL_SKILL_PROMPT = """
## ATTACK SKILL: ACCESS CONTROL / IDOR / AUTH BYPASS

**CRITICAL: Target has authentication, user-specific resources, or role-based
access. Follow this workflow for authorization testing.**

---

### DETECTION: Identify authorization surface

Look for:
- Object IDs in URLs: `/users/123`, `/orders/456`, `/documents/789`
- Sequential/numeric IDs (most vulnerable)
- UUID/GUIDs (harder but still testable)
- Admin endpoints: `/admin`, `/dashboard`, `/settings`
- Role parameters in requests or cookies
- Hidden forms/fields indicating privilege levels

### MANDATORY ACCESS CONTROL WORKFLOW

#### STEP 1: BASELINE

1. Create two user accounts (if possible): User A and User B
2. Log in as User A, access User A's resources — note response
3. Log in as User B, access User B's resources — note response
4. Record: write_note baseline responses for comparison

#### STEP 2: HORIZONTAL PRIVILEGE ESCALATION (IDOR)

Test accessing other users' resources:
1. Log in as User A, try accessing User B's data by changing IDs:
   - `/users/2/profile` (if you're user 1)
   - `/api/orders?user_id=2`
   - `/documents/2/download`
2. Enumerate sequential IDs: 1, 2, 3, 4, 5...
3. Check if responses contain other users' data
4. Try common admin IDs: 1, 0, 100, 1000, admin

If you can see another user's data -> IDOR CONFIRMED.

#### STEP 3: VERTICAL PRIVILEGE ESCALATION

Try to access admin/superuser functions:
1. Force-browse admin endpoints:
   - `/admin`, `/administrator`, `/admin.php`, `/admin.aspx`
   - `/dashboard`, `/control`, `/manage`
   - `/api/admin`, `/api/internal`
2. Modify role parameters:
   - Change `role=user` to `role=admin` in cookies/body
   - Add `is_admin=true` parameter
   - Change JWT claims if using JWT
3. Test HTTP method overrides:
   - `X-HTTP-Method-Override: PUT`
   - `X-HTTP-Method: DELETE`
   - `_method=DELETE` in POST body

#### STEP 4: AUTHENTICATION BYPASS

Test login/auth mechanisms:
1. **Default credentials**: admin/admin, admin/password, root/root, guest/guest
2. **SQL injection bypass**: `admin'--`, `' OR '1'='1'--`
3. **NoSQL injection**: `{"$ne": ""}` in JSON login bodies
4. **JWT attacks**:
   - Algorithm none: `{"alg":"none","typ":"JWT"}` + empty signature
   - Weak secret: try `secret`, `password`, `key` as HMAC secret
   - Change `role` claim from `user` to `admin`
5. **Path normalization bypass**:
   - `/admin` -> `/admin/` (trailing slash)
   - `/admin` -> `/./admin/` (dot segment)
   - `/admin` -> `/admin%2f` (encoded slash)
   - `/admin` -> `/admin..;/` (semicolon trick)
6. **Header-based bypass**:
   - `X-Original-URL: /admin`
   - `X-Rewrite-URL: /admin`
   - `X-Forwarded-For: 127.0.0.1`
   - `X-Custom-IP-Authorization: 127.0.0.1`

#### STEP 5: MASS ASSIGNMENT

Test if you can set privileged fields:
1. During registration, add `role=admin` or `is_admin=true` to the request body
2. During profile update, add `credit_balance=999999`
3. Look for API endpoints that accept PATCH with extra fields

#### ANTI-PATTERNS:
- Do NOT claim IDOR unless you can actually see another user's data
- Always test with two different user accounts (not just unauthenticated)
- Record every endpoint that shows authorization flaws with record_finding
- The difference between 403 (Forbidden) and 404 (Not Found) is significant — 404 may mean the resource doesn't exist, 403 means it exists but you're blocked
"""
