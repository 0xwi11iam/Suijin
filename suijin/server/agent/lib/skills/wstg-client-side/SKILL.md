---
name: WSTG Client-side Testing
description: "Test DOM XSS, HTML5 storage, WebSocket, CORS, postMessage, local file access"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - attack-cors
  - attack-prototype-pollution
cwe_ids:
  - CWE-79
  - CWE-942
  - CWE-346
chains_with:
  - attack-cors
  - attack-prototype-pollution
---

# WSTG Client-side Testing

Test DOM XSS, HTML5 storage, WebSocket, CORS, postMessage, local file access

## Test Areas

1. Test for DOM-based XSS via source/sink analysis
2. Test for HTML5 Web Storage vulnerabilities (localStorage/sessionStorage)
3. Test for WebSocket implementation vulnerabilities
4. Test for Cross-Origin Resource Sharing (CORS) misconfiguration
5. Test for postMessage vulnerabilities (missing origin validation)
6. Test for local file inclusion via client-side code

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
