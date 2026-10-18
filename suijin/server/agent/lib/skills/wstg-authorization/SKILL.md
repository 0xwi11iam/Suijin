---
name: WSTG Authorization Testing
description: "Test directory traversal, privilege escalation, business logic, IDOR, horizontal/vertical access"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - wstg-injection
  - attack-idor-automation
cwe_ids:
  - CWE-22
  - CWE-639
  - CWE-284
  - CWE-285
chains_with:
  - wstg-injection
  - attack-idor-automation
---

# WSTG Authorization Testing

Test directory traversal, privilege escalation, business logic, IDOR, horizontal/vertical access

## Test Areas

1. Test directory traversal/file inclusion (../, ..\, URL-encoded, null byte)
2. Test for bypassing authorization schema (horizontal: same role different user)
3. Test for privilege escalation (vertical: lower role accessing admin functions)
4. Test for IDOR (Insecure Direct Object Reference) on all object references
5. Test for business logic bypass (negative prices, step skipping)
6. Test for failing open (error handling that grants access)
7. Test for multi-tenant isolation (tenant A accessing tenant B's data)

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
