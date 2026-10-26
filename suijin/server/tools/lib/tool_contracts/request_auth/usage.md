**WHEN**: You hit a 401, 403, or a redirect to a login page — ANY time you need authenticated access.

**WHY NOT ask_operator**: ask_operator is for questions about scope, strategy, or decisions.
For credentials/auth, request_auth gives the operator an interactive picker (numbered choices)
and handles the credential handoff automatically. If you ask_operator for credentials, it will
be redirected to this picker — save a turn and call request_auth directly.

**HOW**:
```json
{"action": "use_tool", "tool_name": "request_auth", "tool_args": {"target": "api.example.com", "hint": "login form at /login", "login_url": "https://api.example.com/login"}, "thought": "..."}
```

**WHAT THE OPERATOR SEES**:
- [1] Provide credentials (type: 1 user:password)
- [2] Paste a token/cookie/API key
- [3] Browser login (a Chromium window opens, they log in — 2FA/CAPTCHA handled)
- [4] Skip (test unauthenticated only)

**AFTER**: The credential flows back in the answer. Store it:
```
creds_add(name="my_session", type="cookie", value="<the cookie header>")
```
Then use it: `http_request(url="https://target/api", credential="my_session")`

**NEVER**: guess passwords, try common credentials, or use ask_operator for auth.
