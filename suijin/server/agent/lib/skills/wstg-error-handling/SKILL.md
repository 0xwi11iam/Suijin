---
name: WSTG Error Handling Testing
description: "Test for stack traces, verbose errors, CWE search, improper error handling"
category: "recon"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - wstg-info-gathering
cwe_ids:
  - CWE-209
  - CWE-756
chains_with:
  - wstg-info-gathering
---

# WSTG Error Handling Testing

Test for stack traces, verbose errors, CWE search, improper error handling

## Test Areas

1. Test for stack traces and debug information in error responses
2. Test for improper error handling that reveals internal paths
3. Test for CWE search in error messages (known vulnerability patterns)

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
