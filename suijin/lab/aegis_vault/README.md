# Aegis Vault — hardened SaaS estate, six stages to RCE

A document-signing SaaS that LOOKS like it was built by a security-aware
team — CSP/HSTS, CSRF double-submit, signed sessions, account lockout,
rate limits, a payload-string WAF, HMAC-verified backups, decoys with
canary trips. Every control is real. Every control loses to the chain.

**Public app :5060 · internal mgmt :5050 (127.0.0.1 only)**

```bash
python3 suijin/lab/aegis_vault/app.py        # public estate
python3 suijin/lab/aegis_vault/internal.py   # internal mgmt (separate term)
```

The flag file's NAME is randomized per boot — the final RCE must list
before it reads.

## The chain (no stage solvable without the previous product)

| Stage | Flaw class | Product |
|---|---|---|
| S0 recon | information inventory (robots decoys, JS bundle, legacy /api/v1 diagnostics, enterprise-connectors doc) | the map + the integration-header hint |
| S1 auth | predictable password-reset tokens (sha256(user·day·pepper), 4-hex pepper; API leaks an 8-hex "verification prefix") — offline brute | svc-notifications session |
| S2 webhook | authenticated outbound fetcher w/ custom headers (rate-limited, scheme/port allowlist, hostname blocklist, no redirects) | the fetch primitive |
| S3 SSRF | blocklist matches STRINGS — decimal/octal/IPv6-mapped IP literals slip it (`http://2130706433:5050/`) | internal mgmt access |
| S4 internal trust | network-position auth (`X-Internal-Net: true`) — config dump + session-mint for ANY principal | BACKUP_KEY + admin session |
| S5 RCE | the restore sink: HMAC-SHA256 signature is REAL (wrong key/sig = 403)… behind it, `pickle.loads` of the manifest's `state_file` | code exec; flag surfaces in the restore log |

## Blue's surface

- typed events: `AEGIS_EVENTS_LOG` (default `/tmp/aegis_events.jsonl`) —
  `auth_fail`, `account_locked`, `waf_block`, `ssrf_attempt`,
  `canary_trip`, `internal_config_read`, `session_minted`,
  `restore_bad_sig`, `restore_done`
- traffic JSONL: `AEGIS_TRAFFIC_LOG` (default `/tmp/aegis_traffic.jsonl`)
- levers (`aegis_defense.json`, honored per-request): `waf_enabled`,
  `login_rate_limit`, `lockout_threshold`, `ssrf_strict` (kills the
  notation bypasses), `block_backup_restore` (incident mode),
  `canary_trips`

## Difficulty notes

- nothing is reachable by string-payloads alone — the WAF eats
  `union select` / `<script` / `../` / `os.system` / `subprocess` /
  `__reduce__` in TEXT surfaces; the pickle rides a signed binary
- login is rate-limited per IP and locks accounts at 5 fails — the
  reset lane is the only sane route in
- the webhook refuses redirects, non-80/443/5050 ports, `authorization`
  headers, and every BLOCKLISTED hostname string
- the admin route demands session + CSRF + a valid HMAC over the exact
  archive bytes — the key never leaves the internal network

Local-only (127.0.0.1) by design. Authorized-security-testing lab.
