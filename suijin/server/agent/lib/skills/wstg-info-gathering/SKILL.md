---
name: WSTG Information Gathering
description: "Systematic information gathering: search engine discovery, web server fingerprinting, review page content for info leaka"
category: "recon"
version: "1.0"
author: "suijin-wstg-generator"
tags:
  - wstg-auth-testing
  - wstg-session-mgmt
cwe_ids:
  - CWE-200
chains_with:
  - wstg-auth-testing
  - wstg-session-mgmt
---

# WSTG Information Gathering

Systematic information gathering: search engine discovery, web server fingerprinting, review page content for info leakage, enumerate applications, review robot.txt, fingerprint frameworks, search public files

## Test Areas

1. Search engine discovery (Google dorking for the target)
2. Web server fingerprinting (Server header, X-Powered-By, error pages)
3. Review meta tags and comments for version/info leakage
4. Enumerate applications on the web server (virtual hosts, subdomains)
5. Review robots.txt and sitemap.xml for hidden paths
6. Fingerprint the web application framework (cookies, URLs, HTML patterns)
7. Search for public files (.git, .env, .bak, backup archives)

## Evidence Requirements
- Every finding must have a baseline/exploit diff (http_replay compare)
- 200 OK for both = NOT a vulnerability (no impact difference)
- Filter/blocking = signal to try variants, not a dead end
- Record confirmed findings with catalog_exploit
