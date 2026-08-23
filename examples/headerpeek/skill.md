# HeaderPeek

Static security-header analysis. Feed it a response's headers (from
`http_request` output) and get:

- `header_audit` — missing/weak security headers with remediation notes
- `cors_verdict` — CORS verdict including the wildcard+credentials killer
- `security_score` — 0-100 posture score

Pure functions, zero network. Pairs naturally with http_request during
recon: fetch, then analyze.
