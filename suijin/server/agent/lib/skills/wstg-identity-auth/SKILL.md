---
name: WSTG Identity & Authentication Testing
description: "Test credential encryption in transit, authentication bypass, weak lockout, password reset, security question, multi-fac"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - wstg-session-mgmt
  - wstg-authorization
cwe_ids:
  - CWE-287
  - CWE-307
  - CWE-613
chains_with:
  - wstg-session-mgmt
  - wstg-authorization
---

# WSTG Identity & Authentication Testing

Test credential encryption in transit, authentication bypass, weak lockout, password reset, security question, multi-factor

## Test Areas

1. Test for credential transport over encrypted channel
2. Test for user enumeration via login error messages
3. Test for weak password policy and complexity requirements
4. Test for authentication bypass (SQL injection in login, blank password, default creds)
5. Test for vulnerable remember-me / persistent cookies
6. Test for weak account lockout (brute force resistance)
7. Test for weak password reset (predictable tokens, email enumeration)
8. Test for weak security questions (guessable answers)
9. Test for weak multi-factor implementation (bypassable MFA)
10. Test for race conditions in authentication flows

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
