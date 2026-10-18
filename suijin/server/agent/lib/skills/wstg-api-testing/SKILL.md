---
name: WSTG API Testing
description: "Test REST/SOAP/GraphQL APIs for mass assignment, injection, excessive data exposure, rate limiting"
category: "exploitation"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - attack-graphql
  - attack-mass-assignment
cwe_ids:
  - CWE-915
  - CWE-89
chains_with:
  - attack-graphql
  - attack-mass-assignment
---

# WSTG API Testing

Test REST/SOAP/GraphQL APIs for mass assignment, injection, excessive data exposure, rate limiting

## Test Areas

1. Test for mass assignment in REST APIs (role=admin in POST body)
2. Test for excessive data exposure (API returns more than the client needs)
3. Test for lack of rate limiting on sensitive endpoints
4. Test for GraphQL introspection and injection
5. Test for SOAP XML injection and WSDL exposure
6. Test for API versioning vulnerabilities (old versions still accessible)

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
