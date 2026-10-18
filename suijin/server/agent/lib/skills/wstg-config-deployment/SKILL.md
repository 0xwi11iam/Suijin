---
name: WSTG Configuration & Deployment Management Testing
description: "Test network infrastructure config, application platform config, file handling, admin interfaces, HTTP methods, RIA cros"
category: "recon"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - wstg-auth-testing
cwe_ids:
  - CWE-16
  - CWE-489
chains_with:
  - wstg-auth-testing
---

# WSTG Configuration & Deployment Management Testing

Test network infrastructure config, application platform config, file handling, admin interfaces, HTTP methods, RIA cross-domain policy

## Test Areas

1. Test for default credentials on admin interfaces
2. Test HTTP methods enabled (TRACE, PUT, DELETE, CONNECT)
3. Test RIA cross-domain policy (crossdomain.xml, clientaccesspolicy.xml)
4. Test file permission and access control on static files
5. Test for exposed admin interfaces at common paths (/admin, /console, /manager)
6. Test for old/backup files at predictable paths

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
