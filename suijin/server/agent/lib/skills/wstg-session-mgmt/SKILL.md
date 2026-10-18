---
name: WSTG Session Management Testing
description: "Test session tokens for entropy, predictability, fixation, logout, timeout, cookie scope"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - wstg-identity-auth
cwe_ids:
  - CWE-384
  - CWE-613
  - CWE-539
chains_with:
  - wstg-identity-auth
---

# WSTG Session Management Testing

Test session tokens for entropy, predictability, fixation, logout, timeout, cookie scope

## Test Areas

1. Test for session token entropy and predictability
2. Test for cookies set with insecure attributes (missing HttpOnly, Secure, SameSite)
3. Test for session fixation (token doesn't change after login)
4. Test for session logout (session destroyed on logout)
5. Test for session timeout (idle sessions expire)
6. Test for session puzzlement (sessions shared across users)
7. Test for CSRF tokens in session-modifying requests

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
