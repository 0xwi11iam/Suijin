---
name: WSTG Data Validation Testing
description: "Test reflected/stored/DOM XSS, SQL injection, LDAP injection, ORM injection, command injection, format string"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - attack-sql-injection
  - attack-prototype-pollution
cwe_ids:
  - CWE-79
  - CWE-89
  - CWE-90
  - CWE-77
  - CWE-134
chains_with:
  - attack-sql-injection
  - attack-prototype-pollution
---

# WSTG Data Validation Testing

Test reflected/stored/DOM XSS, SQL injection, LDAP injection, ORM injection, command injection, format string

## Test Areas

1. Test for Reflected XSS (all input vectors: URL params, HTTP headers, form fields)
2. Test for Stored XSS (user input that renders for other users)
3. Test for DOM-based XSS (client-side sink functions)
4. Test for SQL Injection in all data entry points (classic, blind, time-based)
5. Test for LDAP Injection (search filters, DN injection)
6. Test for ORM Injection (HQL, JPQL, Criteria API)
7. Test for OS Command Injection (metacharacters in system calls)
8. Test for Format String Injection (%s, %n in user-controlled format strings)
9. Test for incubated vulnerabilities (stored payloads that execute later)
10. Test for HTTP Splitting/Smuggling (CRLF injection in headers)

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
