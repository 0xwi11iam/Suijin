---
name: WSTG Business Logic Testing
description: "Test business logic data validation, ability to forge requests, integrity checks, process timing, function limit"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - attack-race-condition
cwe_ids:
  - CWE-840
  - CWE-841
chains_with:
  - attack-race-condition
---

# WSTG Business Logic Testing

Test business logic data validation, ability to forge requests, integrity checks, process timing, function limit

## Test Areas

1. Test for business logic bypass in data validation
2. Test for ability to forge requests (price manipulation, quantity overflow)
3. Test for integrity checks and tampering detection
4. Test for process timing exploits (race conditions, TOCTOU)
5. Test for function limit bypass (retry limits, rate limits, captcha bypass)
6. Test for trust boundary violations between user and admin functions

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
