---
name: WSTG Cryptography Testing
description: "Test for weak SSL/TLS, padding oracle, weak encryption, clear-text transmission"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
cwe_ids:
  - CWE-327
  - CWE-326
  - CWE-310
---

# WSTG Cryptography Testing

Test for weak SSL/TLS, padding oracle, weak encryption, clear-text transmission

## Test Areas

1. Test for weak SSL/TLS ciphers and protocols
2. Test for padding oracle vulnerabilities in encrypted responses
3. Test for sensitive information sent via unencrypted channels
4. Test for weak encryption keys (predictable, short, hardcoded)

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
