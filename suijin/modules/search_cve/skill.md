# CVE Search (`search_cve`)

Query NIST NVD for CVEs by software + version. Use after every fingerprint.

## Installation

Built-in. Optional: get NVD API key (50 req/30s instead of 5) at https://nvd.nist.gov/developers/request-an-api-key

## Parameters

- `software` (required) — product name e.g. `apache httpd`
- `version` (optional) — e.g. `2.4.49`
- `limit` (optional, default 5) — max results

## Workflows

```json
{"tool": "search_cve", "args": {"software": "apache httpd", "version": "2.4.49", "limit": 5}}
```

## Output

```
[CVE-2021-41773] HIGH (7.5)
  Path traversal vulnerability in Apache HTTP Server 2.4.49
  CWE: CWE-22
  [Exploit] https://www.exploit-db.com/exploits/50383
```

## CVSS Severity

| Score | Severity | Action |
|-------|----------|--------|
| 9.0+ | CRITICAL | Exploit immediately |
| 7.0-8.9 | HIGH | High priority |
| 4.0-6.9 | MEDIUM | Investigate |
| 0-3.9 | LOW | Skip |
